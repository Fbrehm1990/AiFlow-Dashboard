"""Leads & inbox for Foreman AI: website leads, Gmail, Outlook mail and the Outlook calendar in one place.

Sources (everything is read-only; nothing is sent, deleted, marked read or added to a calendar from here):
  * Website leads   the patrioticwebsites.com Supabase project, read as the site owner (the leads table only lets
                    the owner read it). We keep a refresh token, never the password.
  * Gmail           IMAP with a Google app password, inbox opened read-only.
  * Outlook mail    Microsoft Graph (Mail.Read, Calendars.Read), signed in with a device code. Needs the
                    Application (client) ID of an app the owner registers once with Microsoft.
  * Outlook calendar  Graph when Outlook is signed in; otherwise the calendar's published ICS link.

Dates and times mentioned in leads and emails ("can we meet Tuesday at 3?") are matched against the calendar.
The page offers an "Add to Outlook" link that opens Outlook's new-event form filled in; the owner presses Save.

Settings live in tasks/connections.json (gitignored). Passwords, tokens and the ICS link are encrypted with
Windows DPAPI, so they only decrypt for this Windows user on this PC, and are never sent to the browser.
"""
import base64
import concurrent.futures as cf
import datetime as dt
import email
import email.policy
import email.utils
import html
import imaplib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "tasks" / "connections.json"
PATRIOT = ROOT.parent / "Patriot" / "chat.js"      # the website's own code, used to prefill the Supabase project
UA = "Foreman-AI/1.0"
MS_AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
MS_SCOPE = "offline_access User.Read Mail.Read Calendars.Read"
GRAPH = "https://graph.microsoft.com/v1.0"
CACHE_TTL = 120
DAYS_MAIL = 14
DAYS_CAL = 14

_lock = threading.RLock()
_cache = {"at": 0, "data": None}
_tokens = {}            # in-memory access tokens: name -> (token, expires_at)
_device = {}            # pending Outlook device-code sign-in


class InboxError(ValueError):
    pass


# ---------------------------------------------------------------- secrets (Windows DPAPI)
if os.name == "nt":
    import ctypes
    import ctypes.wintypes as wt

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(data, protect):
        buf = ctypes.create_string_buffer(data, len(data))
        src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = _Blob()
        fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):   # 0x1: never show UI
            raise InboxError("Windows couldn't unlock a saved sign-in. Connect that account again.")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def _enc(s):
        return "dpapi:" + base64.b64encode(_dpapi(s.encode(), True)).decode()

    def _dec(v):
        if not v:
            return ""
        if v.startswith("dpapi:"):
            return _dpapi(base64.b64decode(v[6:]), False).decode()
        return v[6:] if v.startswith("plain:") else v
else:                                                    # not Windows: stored as-is in the gitignored file
    def _enc(s):
        return "plain:" + s

    def _dec(v):
        return (v or "")[6:] if (v or "").startswith("plain:") else (v or "")


# ---------------------------------------------------------------- settings
def config():
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(c):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2), encoding="utf-8")
    os.replace(tmp, CFG)
    _cache["at"] = 0


def site_defaults():
    """The website's Supabase project URL and public (publishable) key, read from the site's own chat.js."""
    try:
        s = PATRIOT.read_text(encoding="utf-8")
    except OSError:
        return {}
    url = re.search(r"SUPABASE_URL\s*=\s*'(https://[a-z0-9]+\.supabase\.co)'", s)
    key = re.search(r"SUPABASE_KEY\s*=\s*'(sb_publishable_[A-Za-z0-9_\-]+)'", s)
    return {"url": url.group(1), "key": key.group(1)} if url and key else {}


# ---------------------------------------------------------------- HTTP
def _http(method, url, data=None, headers=None, form=False, timeout=20):
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        err["_status"] = e.code
        return err, e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise InboxError(f"Couldn't reach {urllib.parse.urlparse(url).hostname} ({e.__class__.__name__})")
    if "json" in ctype:
        return json.loads(raw or b"{}"), 200
    return raw.decode("utf-8", "replace"), 200


# ---------------------------------------------------------------- website leads (Supabase)
def setup_site(url, key, email_addr, password):
    url, key, email_addr = str(url or "").strip().rstrip("/"), str(key or "").strip(), str(email_addr or "").strip()
    if not re.fullmatch(r"https://[a-z0-9]+\.supabase\.co", url):
        raise InboxError("The project address should look like https://xxxx.supabase.co")
    if not re.fullmatch(r"(sb_publishable_|eyJ)[A-Za-z0-9_\-.]{10,400}", key):
        raise InboxError("Use the project's publishable (public) key, the same one the website uses")
    if not email_addr or not password:
        raise InboxError("Enter the email and password you use to sign in to your website's dashboard")
    r, code = _http("POST", f"{url}/auth/v1/token?grant_type=password",
                    {"email": email_addr, "password": str(password)}, {"apikey": key})
    if code != 200 or "refresh_token" not in r:
        raise InboxError("Supabase didn't accept that email and password.")
    with _lock:
        c = config()
        c["site"] = {"url": url, "key": key, "email": email_addr, "refresh": _enc(r["refresh_token"])}
        _tokens["site"] = (r["access_token"], time.time() + int(r.get("expires_in", 3600)) - 60)
        _save(c)
    _leads(config())            # proves the account can read the leads table
    return status(force=True)


def _site_token(c):
    tok = _tokens.get("site")
    if tok and tok[1] > time.time():
        return tok[0]
    with _lock:
        c = config()
        s = c["site"]
        r, code = _http("POST", f"{s['url']}/auth/v1/token?grant_type=refresh_token",
                        {"refresh_token": _dec(s["refresh"])}, {"apikey": s["key"]})
        if code != 200 or "access_token" not in r:
            raise InboxError("The website sign-in expired. Connect website leads again.")
        s["refresh"] = _enc(r["refresh_token"])          # Supabase rotates refresh tokens
        _save(c)
        _tokens["site"] = (r["access_token"], time.time() + int(r.get("expires_in", 3600)) - 60)
        return r["access_token"]


def _leads(c):
    s = c["site"]
    tok = _site_token(c)
    r, code = _http("GET", f"{s['url']}/rest/v1/leads?select=*&order=created_at.desc&limit=40",
                    headers={"apikey": s["key"], "Authorization": f"Bearer {tok}"})
    if code != 200 or not isinstance(r, list):
        raise InboxError("Couldn't read the leads table. Make sure this account is the site owner.")
    keep = ("id", "name", "email", "phone", "business", "business_type", "timeline", "message", "status",
            "source", "created_at", "need", "current_website")
    return [{k: row.get(k) for k in keep if row.get(k) not in (None, "")} for row in r]


# ---------------------------------------------------------------- Gmail (IMAP, read-only)
def setup_gmail(email_addr, password):
    email_addr = str(email_addr or "").strip()
    pw = re.sub(r"\s+", "", str(password or ""))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email_addr, re.I):
        raise InboxError("Enter your Gmail address")
    if not re.fullmatch(r"[a-z]{16}", pw):
        raise InboxError("That isn't an app password. App passwords are 16 letters, made at myaccount.google.com/apppasswords.")
    _imap_login(email_addr, pw).logout()
    with _lock:
        c = config()
        c["gmail"] = {"email": email_addr, "password": _enc(pw)}
        _save(c)
    return status(force=True)


def _imap_login(addr, pw):
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
    except OSError:
        raise InboxError("Couldn't reach Gmail")
    try:
        m.login(addr, pw)
    except imaplib.IMAP4.error:
        raise InboxError("Gmail didn't accept that address and app password.")
    return m


def _text_of(msg):
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        t = part.get_content()
        if part.get_content_type() == "text/html":
            t = _strip_html(t)
        return t
    except Exception:
        return ""


def _strip_html(t):
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", t)
    return html.unescape(re.sub(r"<[^>]+>", " ", t))


def _gmail(c):
    g = c["gmail"]
    m = _imap_login(g["email"], _dec(g["password"]))
    out = []
    try:
        m.select("INBOX", readonly=True)                     # read-only: nothing gets marked as read
        since = (dt.date.today() - dt.timedelta(days=DAYS_MAIL)).strftime("%d-%b-%Y")
        typ, data = m.uid("search", None, "SINCE", since)
        uids = (data[0] or b"").split()[-25:]
        for uid in reversed(uids):
            typ, d = m.uid("fetch", uid, "(FLAGS BODY.PEEK[]<0.80000>)")
            meta = next((x for x in d if isinstance(x, tuple)), None)
            if not meta:
                continue
            msg = email.message_from_bytes(meta[1], policy=email.policy.default)
            name, addr = email.utils.parseaddr(str(msg.get("From", "")))
            try:
                when = email.utils.parsedate_to_datetime(str(msg.get("Date"))).astimezone()
            except (TypeError, ValueError):
                when = None
            mid = str(msg.get("Message-ID", "")).strip()
            out.append({"acct": "gmail", "from_name": name or addr, "from_addr": addr,
                        "subject": str(msg.get("Subject", "")) or "(no subject)",
                        "date": when.isoformat(timespec="seconds") if when else None,
                        "unread": b"\\Seen" not in meta[0],
                        "bulk": bool(msg.get("List-Unsubscribe")) or _bulk_sender(addr),
                        "text": _clean_reply(_text_of(msg))[:4000],
                        "link": f"https://mail.google.com/mail/u/?authuser={urllib.parse.quote(g['email'])}#search/"
                                + urllib.parse.quote(f"rfc822msgid:{mid.strip('<>')}") if mid else
                                "https://mail.google.com/"})
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- Outlook (Microsoft Graph)
def outlook_start(client_id):
    client_id = str(client_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", client_id):
        raise InboxError("The Application (client) ID looks like 1a2b3c4d-1234-5678-9abc-0123456789ab")
    r, code = _http("POST", f"{MS_AUTH}/devicecode", {"client_id": client_id, "scope": MS_SCOPE}, form=True)
    if code != 200 or "device_code" not in r:
        hint = r.get("error_description", "").split("\r\n")[0] if isinstance(r, dict) else ""
        raise InboxError("Microsoft didn't accept that app. Check that it allows personal Microsoft accounts "
                         "and has 'Allow public client flows' turned on. " + hint)
    _device.clear()
    _device.update({"client_id": client_id, "device_code": r["device_code"], "interval": int(r.get("interval", 5)),
                    "expires": time.time() + int(r.get("expires_in", 900))})
    return {"user_code": r["user_code"], "verification_uri": r.get("verification_uri", "https://microsoft.com/devicelogin")}


def outlook_poll():
    if not _device or _device["expires"] < time.time():
        raise InboxError("That sign-in code expired. Start again.")
    r, code = _http("POST", f"{MS_AUTH}/token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                                 "client_id": _device["client_id"], "device_code": _device["device_code"]},
                    form=True)
    if code != 200:
        err = r.get("error") if isinstance(r, dict) else ""
        if err in ("authorization_pending", "slow_down"):
            return {"pending": True}
        _device.clear()
        raise InboxError("Outlook sign-in didn't finish (" + (err or "error") + "). Start again.")
    _tokens["outlook"] = (r["access_token"], time.time() + int(r.get("expires_in", 3600)) - 60)
    me, _ = _http("GET", f"{GRAPH}/me?$select=mail,userPrincipalName", headers={"Authorization": f"Bearer {r['access_token']}"})
    with _lock:
        c = config()
        c["outlook"] = {"client_id": _device["client_id"], "refresh": _enc(r["refresh_token"]),
                        "email": (me or {}).get("mail") or (me or {}).get("userPrincipalName") or ""}
        _save(c)
    _device.clear()
    return status(force=True)


def _ms_token(c):
    tok = _tokens.get("outlook")
    if tok and tok[1] > time.time():
        return tok[0]
    with _lock:
        c = config()
        o = c["outlook"]
        r, code = _http("POST", f"{MS_AUTH}/token", {"grant_type": "refresh_token", "client_id": o["client_id"],
                                                     "refresh_token": _dec(o["refresh"]), "scope": MS_SCOPE}, form=True)
        if code != 200 or "access_token" not in r:
            raise InboxError("The Outlook sign-in expired. Sign in to Outlook again.")
        if r.get("refresh_token"):
            o["refresh"] = _enc(r["refresh_token"])
            _save(c)
        _tokens["outlook"] = (r["access_token"], time.time() + int(r.get("expires_in", 3600)) - 60)
        return r["access_token"]


def _graph(c, path):
    r, code = _http("GET", GRAPH + path, headers={"Authorization": f"Bearer {_ms_token(c)}"})
    if code != 200:
        raise InboxError("Outlook returned an error (" + str((r or {}).get("error", {}).get("code", code)) + ")")
    return r


def _outlook_mail(c):
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=DAYS_MAIL)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.urlencode({"$top": "25", "$orderby": "receivedDateTime desc",
                                "$filter": f"receivedDateTime ge {since}",
                                "$select": "subject,from,receivedDateTime,isRead,body,webLink,inferenceClassification"})
    r = _graph(c, "/me/mailFolders/inbox/messages?" + q)
    out = []
    for m in r.get("value", []):
        f = (m.get("from") or {}).get("emailAddress") or {}
        body = (m.get("body") or {})
        text = body.get("content", "")
        if body.get("contentType") == "html":
            text = _strip_html(text)
        when = _parse_iso(m.get("receivedDateTime"))
        out.append({"acct": "outlook", "from_name": f.get("name") or f.get("address", ""), "from_addr": f.get("address", ""),
                    "subject": m.get("subject") or "(no subject)", "date": when.isoformat(timespec="seconds") if when else None,
                    "unread": not m.get("isRead", True),
                    "bulk": m.get("inferenceClassification") == "other" or _bulk_sender(f.get("address", "")),
                    "text": _clean_reply(text)[:4000], "link": m.get("webLink") or "https://outlook.live.com/mail/"})
    return out


def _outlook_events(c):
    start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
    end = start + dt.timedelta(days=DAYS_CAL + 1)
    q = urllib.parse.urlencode({"startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "$top": "200",
                                "$orderby": "start/dateTime",
                                "$select": "subject,start,end,isAllDay,location,webLink,showAs,isCancelled"})
    r = _graph(c, "/me/calendarView?" + q)
    out = []
    for e in r.get("value", []):
        if e.get("isCancelled"):
            continue
        s, en = _graph_time(e.get("start")), _graph_time(e.get("end"))
        if not s:
            continue
        out.append({"title": e.get("subject") or "(busy)", "start": s, "end": en or s, "allday": bool(e.get("isAllDay")),
                    "location": ((e.get("location") or {}).get("displayName") or ""), "free": e.get("showAs") == "free",
                    "link": e.get("webLink") or "https://outlook.live.com/calendar/"})
    return out


def _graph_time(t):
    if not t or not t.get("dateTime"):
        return None
    d = dt.datetime.fromisoformat(t["dateTime"][:19])
    tz = t.get("timeZone", "UTC")
    return (d.replace(tzinfo=dt.timezone.utc) if tz.upper() == "UTC" else d.astimezone()).astimezone()


# ---------------------------------------------------------------- Outlook calendar (published ICS link)
def setup_ics(url):
    url = str(url or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[9:]
    if not re.fullmatch(r"https://[^\s]+", url) or not url.lower().split("?")[0].endswith(".ics"):
        raise InboxError("Paste the ICS link (it ends in .ics) from Outlook's Publish a calendar setting")
    txt, code = _http("GET", url, timeout=25)
    if code != 200 or not isinstance(txt, str) or "BEGIN:VCALENDAR" not in txt:
        raise InboxError("That link didn't return a calendar. Copy the ICS link, not the HTML one.")
    with _lock:
        c = config()
        c["ics"] = {"url": _enc(url)}
        _save(c)
    return status(force=True)


WIN_TZ = {"Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
          "Mountain Standard Time": "America/Denver", "US Mountain Standard Time": "America/Phoenix",
          "Pacific Standard Time": "America/Los_Angeles", "Alaskan Standard Time": "America/Anchorage",
          "Hawaiian Standard Time": "Pacific/Honolulu", "GMT Standard Time": "Europe/London", "UTC": "UTC"}


def _ics_time(val, params):
    """-> (aware datetime, allday)"""
    val = val.strip()
    if "VALUE=DATE" in params and "VALUE=DATE-TIME" not in params or re.fullmatch(r"\d{8}", val):
        d = dt.datetime.strptime(val[:8], "%Y%m%d")
        return d.astimezone(), True
    d = dt.datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
    if val.endswith("Z"):
        return d.replace(tzinfo=dt.timezone.utc).astimezone(), False
    m = re.search(r"TZID=\"?([^\";:]+)", params)
    if m:
        name = WIN_TZ.get(m.group(1), m.group(1))
        try:
            from zoneinfo import ZoneInfo
            return d.replace(tzinfo=ZoneInfo(name)).astimezone(), False
        except Exception:
            pass
    return d.astimezone(), False


DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _occurrences(start, rule, exdates, lo, hi):
    """Expand the common RRULE shapes (DAILY / WEEKLY with BYDAY / MONTHLY / YEARLY, INTERVAL, COUNT, UNTIL)."""
    if not rule:
        return [start]
    r = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq, step = r.get("FREQ"), int(r.get("INTERVAL", "1") or 1)
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            until, _ = _ics_time(r["UNTIL"], "")
        except ValueError:
            pass
    byday = [d[-2:] for d in r.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    out, n, cur, guard = [], 0, start, 0
    while guard < 3000:
        guard += 1
        if freq == "WEEKLY" and byday:
            week0 = cur - dt.timedelta(days=cur.weekday())
            cands = sorted(week0 + dt.timedelta(days=DAYS.index(d)) for d in byday)
            cands = [x for x in cands if x >= start]
        else:
            cands = [cur]
        for x in cands:
            if (until and x > until) or (count is not None and n >= count) or x > hi:
                return out
            n += 1
            if x >= lo and x.replace(tzinfo=None) not in exdates:
                out.append(x)
        if freq == "DAILY":
            cur += dt.timedelta(days=step)
        elif freq == "WEEKLY":
            cur += dt.timedelta(weeks=step)
        elif freq == "MONTHLY":
            y, mo = divmod(cur.month - 1 + step, 12)
            try:
                cur = cur.replace(year=cur.year + y, month=mo + 1)
            except ValueError:
                cur = cur.replace(year=cur.year + y, month=mo + 1, day=28)
        elif freq == "YEARLY":
            cur = cur.replace(year=cur.year + step)
        else:
            return out or [start]
    return out


def _ics_events(c):
    txt, code = _http("GET", _dec(c["ics"]["url"]), timeout=25)
    if code != 200 or not isinstance(txt, str):
        raise InboxError("Couldn't load the Outlook calendar link")
    lines = re.sub(r"\r?\n[ \t]", "", txt).splitlines()            # unfold
    now = dt.datetime.now().astimezone()
    lo = now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
    hi = lo + dt.timedelta(days=DAYS_CAL + 1)
    out, ev, moved = [], None, set()
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            ev = {"ex": set()}
        elif ln == "END:VEVENT" and ev is not None:
            if "start" in ev and ev.get("status") != "CANCELLED":
                dur = (ev.get("end") or ev["start"]) - ev["start"]
                if ev.get("recurid"):
                    moved.add((ev.get("uid"), ev["recurid"].replace(tzinfo=None)))
                for s in _occurrences(ev["start"], ev.get("rrule"), ev["ex"], lo, hi):
                    if s + dur >= lo:
                        out.append({"uid": ev.get("uid"), "orig": s.replace(tzinfo=None), "recurring": bool(ev.get("rrule")),
                                    "title": ev.get("summary") or "(busy)", "start": s, "end": s + dur,
                                    "allday": ev.get("allday", False), "location": ev.get("location", ""),
                                    "free": ev.get("transp") == "TRANSPARENT", "link": "https://outlook.live.com/calendar/"})
            ev = None
        elif ev is not None and ":" in ln:
            head, val = ln.split(":", 1)
            name, _, params = head.partition(";")
            val = val.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\N", " ")
            try:
                if name == "DTSTART":
                    ev["start"], ev["allday"] = _ics_time(val, params)
                elif name == "DTEND":
                    ev["end"], _ = _ics_time(val, params)
                elif name == "RECURRENCE-ID":
                    ev["recurid"], _ = _ics_time(val, params)
                elif name == "EXDATE":
                    for v in val.split(","):
                        ev["ex"].add(_ics_time(v, params)[0].replace(tzinfo=None))
            except ValueError:
                continue
            if name == "SUMMARY":
                ev["summary"] = val
            elif name == "LOCATION":
                ev["location"] = val
            elif name == "RRULE":
                ev["rrule"] = val
            elif name == "UID":
                ev["uid"] = val
            elif name == "STATUS":
                ev["status"] = val
            elif name == "TRANSP":
                ev["transp"] = val
    # a moved instance of a recurring meeting replaces the original slot
    out = [e for e in out if not (e["recurring"] and (e["uid"], e["orig"]) in moved)]
    for e in out:
        e.pop("uid", None), e.pop("orig", None), e.pop("recurring", None)
    return sorted(out, key=lambda e: e["start"])


# ---------------------------------------------------------------- spotting dates and times
WD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MEET = re.compile(r"\b(meet|meeting|call|chat|talk|available|availability|appointment|appt|schedule|consult|"
                  r"consultation|zoom|teams|phone|stop by|come by|works for|free on|free at|book)\w*", re.I)
_WD = r"(?:(next|this)\s+)?(mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|urday|rsday)?\b"
_MD = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(20\d\d))?"
_DM = r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
_NUM = r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?(?![\d/])"
_REL = r"\b(today|tonight|tomorrow)\b"
DAY_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, p in enumerate([_WD, _MD, _DM, _NUM, _REL])), re.I)
TIME_RE = re.compile(r"(?:\b(noon|midday)|\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?|\b(\d{1,2}):(\d{2})\b|"
                     r"(?:\bat|@|\baround|\bby)\s*(\d{1,2})(?![\d:/])(?!\s*(?:days?|weeks?|months?|years?|%|percent|people|pages?)))", re.I)


def _clean_reply(t):
    """Drop quoted earlier messages so we only read what this person just wrote."""
    t = t.replace("\r\n", "\n")
    for pat in (r"\n\s*On .{3,300}?wrote:\s*\n", r"\n-{2,}\s*Original Message\s*-{2,}", r"\n_{5,}\s*\n\s*From:",
                r"\nFrom:\s.*\nSent:\s"):
        m = re.search(pat, t, re.I | re.S)
        if m:
            t = t[:m.start()]
    return "\n".join(ln for ln in t.split("\n") if not ln.lstrip().startswith(">")).strip()


def _bulk_sender(addr):
    return bool(re.search(r"(no-?reply|donotreply|newsletter|notifications?|mailer-daemon|marketing|news@|info@.*(mail|e)\.)",
                          addr or "", re.I))


def _resolve_day(m, ref):
    g = m.groupdict()
    today = ref.date()
    if g["g0"]:
        s = m.group("g0").lower()
        mm = re.match(_WD, s, re.I)
        wd = WD[mm.group(2)[:3]]
        ahead = (wd - today.weekday()) % 7
        if ahead == 0 and (mm.group(1) or "").lower() != "this":
            ahead = 7                                # "Tuesday" said on a Tuesday means next week
        return today + dt.timedelta(days=ahead)      # "next Monday" = the coming Monday; the page shows the date
    if g["g1"] or g["g2"]:
        s = m.group("g1") or m.group("g2")
        mm = re.match(_MD, s, re.I) if g["g1"] else re.match(_DM, s, re.I)
        if g["g1"]:
            mon, day, yr = MON[mm.group(1)[:3].lower()], int(mm.group(2)), mm.group(3)
        else:
            mon, day, yr = MON[mm.group(2)[:3].lower()], int(mm.group(1)), None
        return _ymd(int(yr) if yr else None, mon, day, today)
    if g["g3"]:
        mm = re.match(_NUM, m.group("g3"))
        mon, day, yr = int(mm.group(1)), int(mm.group(2)), mm.group(3)
        if not (1 <= mon <= 12 and 1 <= day <= 31):
            return None
        return _ymd((int(yr) + 2000 if len(yr) == 2 else int(yr)) if yr else None, mon, day, today)
    s = m.group("g4").lower()
    return today + dt.timedelta(days=1 if s == "tomorrow" else 0)


def _ymd(yr, mon, day, today):
    try:
        d = dt.date(yr or today.year, mon, day)
    except ValueError:
        return None
    if not yr and d < today - dt.timedelta(days=1):
        try:
            d = dt.date(today.year + 1, mon, day)
        except ValueError:
            return None
    return d


def _resolve_time(m):
    if m.group(1):
        return 12, 0
    if m.group(2):
        h, mi, ap = int(m.group(2)), int(m.group(3) or 0), m.group(4).lower()
        if not (1 <= h <= 12 and mi < 60):
            return None
        return (h % 12) + (12 if ap == "p" else 0), mi
    if m.group(5):
        h, mi = int(m.group(5)), int(m.group(6))
        if h > 23 or mi > 59:
            return None
        return (h + 12 if 1 <= h <= 6 else h), mi             # "3:30" in a business email means the afternoon
    h = int(m.group(7))                                       # "at 3": business hours, so 1-6 is the afternoon
    if not 1 <= h <= 12:
        return None
    return (h + 12 if h <= 6 else h), 0


def find_dates(text, ref):
    """Meeting-like date/time mentions in text written at `ref` -> [(start datetime, has_time, snippet)]."""
    out, seen = [], set()
    now = dt.datetime.now().astimezone()
    for m in DAY_RE.finditer(text or ""):
        day = _resolve_day(m, ref)
        if not day:
            continue
        a, b = m.start(), m.end()
        window = text[max(0, a - 30): b + 40]
        tm = None
        for t in TIME_RE.finditer(text, max(0, a - 30), min(len(text), b + 40)):
            tm = _resolve_time(t)
            if tm:
                break
        sentence = text[max(0, text.rfind(".", 0, a) + 1): (text.find(".", b) + 1 or len(text))][:300]
        if not tm and not MEET.search(sentence):
            continue                                 # a bare date ("invoice due Nov 3") isn't a meeting
        if tm:
            start = dt.datetime(day.year, day.month, day.day, tm[0], tm[1]).astimezone()
        else:
            start = dt.datetime(day.year, day.month, day.day).astimezone()
        if start < now - dt.timedelta(hours=1) or start > now + dt.timedelta(days=120):
            continue
        k = (start, bool(tm))
        if k in seen:
            continue
        seen.add(k)
        out.append((start, bool(tm), re.sub(r"\s+", " ", sentence.strip() or window.strip())[:220]))
        if len(out) >= 3:
            break
    return out


def _add_link(subject, start, has_time, body, minutes=30):
    q = {"path": "/calendar/action/compose", "rru": "addevent", "subject": subject[:200], "body": body[:1500]}
    if has_time:
        q["startdt"] = start.isoformat(timespec="seconds")
        q["enddt"] = (start + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")
    else:
        q["startdt"], q["enddt"], q["allday"] = start.date().isoformat(), (start.date() + dt.timedelta(days=1)).isoformat(), "true"
    return "https://outlook.live.com/calendar/0/deeplink/compose?" + urllib.parse.urlencode(q)


def _check(start, has_time, events):
    if not events:
        return None, ""
    if not has_time:
        same = [e for e in events if e["start"].date() <= start.date() <= e["end"].date() and not e["free"]]
        return (not same), (", ".join(e["title"] for e in same[:2]) if same else "")
    end = start + dt.timedelta(minutes=30)
    clash = [e for e in events if not e["allday"] and not e["free"] and e["start"] < end and e["end"] > start]
    return (not clash), (clash[0]["title"] if clash else "")


# ---------------------------------------------------------------- the snapshot the page polls
def status(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_TTL:
            return _cache["data"]
    c = config()
    conn = {"site": "site" in c, "gmail": "gmail" in c, "outlook": "outlook" in c, "ics": "ics" in c}
    jobs = {}
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        if conn["site"]:
            jobs["leads"] = pool.submit(_leads, c)
        if conn["gmail"]:
            jobs["gmail"] = pool.submit(_gmail, c)
        if conn["outlook"]:
            jobs["outlook"] = pool.submit(_outlook_mail, c)
            jobs["calendar"] = pool.submit(_outlook_events, c)
        elif conn["ics"]:
            jobs["calendar"] = pool.submit(_ics_events, c)
        res, errors = {}, {}
        for k, f in jobs.items():
            try:
                res[k] = f.result(timeout=60)
            except InboxError as e:
                errors[k] = str(e)
            except Exception as e:
                errors[k] = f"Couldn't load ({e.__class__.__name__})"

    leads, events = res.get("leads", []), res.get("calendar", [])
    mail = sorted(res.get("gmail", []) + res.get("outlook", []), key=lambda m: m["date"] or "", reverse=True)

    dates = []
    for ld in leads:
        ref = _parse_iso(ld.get("created_at")) or dt.datetime.now().astimezone()
        for start, has_time, snip in find_dates(" ".join(str(ld.get(k, "")) for k in ("message", "timeline", "need")), ref):
            who = ld.get("name") or ld.get("email") or "Website lead"
            dates.append(_spot("lead", who, ld.get("business") or "Website inquiry", start, has_time, snip, events,
                               f"Website lead: {who}" + (f" ({ld['business']})" if ld.get("business") else ""),
                               f"{ld.get('email', '')} {ld.get('phone', '')}".strip()))
    for m in mail:
        if m["bulk"]:
            continue
        ref = _parse_iso(m["date"]) or dt.datetime.now().astimezone()
        for start, has_time, snip in find_dates(m["subject"] + ". " + m["text"], ref):
            dates.append(_spot(m["acct"], m["from_name"], m["subject"], start, has_time, snip, events,
                               f"{m['subject']} ({m['from_name']})", m["from_addr"]))
    dates.sort(key=lambda d: d["start"])

    iso = lambda t: t.isoformat(timespec="seconds") if t else None
    data = {
        "updated": iso(dt.datetime.now().astimezone()),
        "connected": conn, "accounts": {"site": c.get("site", {}).get("email"), "gmail": c.get("gmail", {}).get("email"),
                                        "outlook": c.get("outlook", {}).get("email")},
        "site_defaults": site_defaults(), "errors": errors,
        "leads": leads,
        "mail": [{k: v for k, v in m.items() if k != "text"} | {"snippet": re.sub(r"\s+", " ", m["text"])[:180]} for m in mail],
        "events": [dict(e, start=iso(e["start"]), end=iso(e["end"])) for e in events if e["end"] >= dt.datetime.now().astimezone() - dt.timedelta(hours=1)],
        "dates": dates,
    }
    with _lock:
        _cache.update(at=time.time(), data=data)
    return data


def _spot(kind, who, subject, start, has_time, snip, events, title, contact):
    free, clash = _check(start, has_time, events)
    body = f"{snip}\n\n{contact}".strip()
    return {"kind": kind, "who": who, "subject": subject, "start": start.isoformat(timespec="seconds"), "has_time": has_time,
            "snippet": snip, "free": free, "conflict": clash, "add": _add_link(title, start, has_time, body)}


def _parse_iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return None


def disconnect(which):
    if which not in ("site", "gmail", "outlook", "ics"):
        raise InboxError("Unknown connection")
    with _lock:
        c = config()
        c.pop(which, None)
        _tokens.pop(which, None)
        _save(c)
    return status(force=True)


if __name__ == "__main__":
    import sys
    ref = dt.datetime.now().astimezone()
    for line in sys.argv[1:] or ["Can we meet Tuesday at 3?", "I'm free Nov 20 at 10:30am for a call",
                                 "Invoice due Nov 3", "Call me tomorrow afternoon", "How about 12/1 @ 2"]:
        print(line, "->", [(s.strftime("%a %b %d %I:%M %p"), t) for s, t, _ in find_dates(line, ref)])

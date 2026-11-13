"""AI usage for the dashboard: tokens, plan limits and what you owe, across Claude Code, Codex and the Anthropic API.

Sources (all read on this PC, nothing leaves it except the Anthropic Admin API call):
  * Claude Code   ~/.claude/projects/**/*.jsonl     tokens per response (de-duplicated), priced at API rates
  * Claude plan   tasks/claude_limits.json          5-hour and weekly % used, written by dashboard/statusline.py
                                                    every time Claude Code updates its status line
  * Codex         ~/.codex/sessions/**/*.jsonl(.zst) tokens per turn + the ChatGPT plan's Codex limit %
  * Anthropic API Admin API cost_report + usage_report   real $ billed this month (needs an Admin key)

Settings live in tasks/usage.json (gitignored). The Admin key is never sent back to the browser.

  python dashboard/usage.py            -> prints the current numbers
"""
import calendar
import datetime as dt
import glob
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "tasks" / "usage.json"
LIMITS = ROOT / "tasks" / "claude_limits.json"
STATUSLINE = Path(__file__).resolve().parent / "statusline.py"
HOME = Path(os.path.expanduser("~"))
CLAUDE_SETTINGS = HOME / ".claude" / "settings.json"

DEFAULT = {
    "claude_plan": {"name": "Claude Max 5x", "monthly_fee": 100.0, "billing_day": 1},
    "codex_plan": {"name": "ChatGPT Go", "monthly_fee": 8.0, "billing_day": 1, "on": True},
    "admin_key": "",
}

# USD per million tokens, used only for "what Claude Code would cost at API rates". Longest match wins.
PRICES = {
    "opus-5-5":   (4.00, 20.00, 5.00, 0.20),
    "opus-5":     (5.00, 25.00, 6.25, 0.50),
    "opus-4":     (5.00, 25.00, 6.25, 0.50),
    "sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "sonnet-5":   (2.00, 10.00, 2.50, 0.20),
    "sonnet-4":   (3.00, 15.00, 3.75, 0.30),
    "haiku-5-5":  (0.10, 0.50, 0.125, 0.01),
    "haiku-4":    (1.00, 5.00, 1.25, 0.10),
    "fable":      (10.00, 50.00, 12.50, 1.00),
}
DEFAULT_PRICE = PRICES["opus-5-5"]

_lock = threading.Lock()


# ---------------------------------------------------------------- settings
def config():
    try:
        c = json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    out = json.loads(json.dumps(DEFAULT))
    for k, v in c.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def _save(c):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2), encoding="utf-8")
    os.replace(tmp, CFG)


def update(body):
    """Plan names/fees/billing days and the Admin key, from the dashboard's settings form."""
    c = config()
    for plan in ("claude_plan", "codex_plan"):
        p = body.get(plan)
        if not isinstance(p, dict):
            continue
        if "name" in p:
            c[plan]["name"] = str(p["name"])[:40].strip() or DEFAULT[plan]["name"]
        if "monthly_fee" in p:
            try:
                fee = float(str(p["monthly_fee"]).replace("$", "").replace(",", "").strip())
            except ValueError:
                raise ValueError("Monthly fee must be a number, like 100 or 8.00")
            if not 0 <= fee <= 10000:
                raise ValueError("Monthly fee must be between 0 and 10,000")
            c[plan]["monthly_fee"] = round(fee, 2)
        if "billing_day" in p:
            try:
                day = int(str(p["billing_day"]).strip())
            except ValueError:
                raise ValueError("Renews on day must be a number from 1 to 31")
            if not 1 <= day <= 31:
                raise ValueError("Billing day must be 1–31")
            c[plan]["billing_day"] = day
        if plan == "codex_plan" and "on" in p:
            c[plan]["on"] = bool(p["on"])
    if "admin_key" in body:
        key = str(body["admin_key"] or "").strip()
        if key and not key.startswith("sk-ant-admin"):
            raise ValueError("That isn't an Admin API key. Admin keys start with sk-ant-admin.")
        if key:
            _anthropic_get("/v1/organizations/cost_report", {"starting_at": _month_start_utc(), "limit": 1}, key)
        c["admin_key"] = key
        _API_CACHE.clear()
    _save(c)
    return status()


# ---------------------------------------------------------------- helpers
def _parse_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return None


def _price(model):
    m, best, n = (model or "").lower(), DEFAULT_PRICE, 0
    for k, p in PRICES.items():
        if k in m and len(k) > n:
            best, n = p, len(k)
    return best


def _short_model(m):
    parts = [p for p in m.lower().replace("claude-", "").split("-") if not (p.isdigit() and len(p) == 8)]
    if not parts:
        return m
    return (parts[0].capitalize() + " " + ".".join(p for p in parts[1:] if p.isdigit())).strip()


def _cycle(billing_day, today):
    def at(y, m):
        return dt.date(y, m, min(billing_day, calendar.monthrange(y, m)[1]))
    start = at(today.year, today.month)
    if start > today:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start = at(y, m)
    y, m = (start.year, start.month + 1) if start.month < 12 else (start.year + 1, 1)
    return start, at(y, m)


def _month_start_utc():
    n = dt.datetime.now(dt.timezone.utc)
    return n.strftime("%Y-%m-01T00:00:00Z")


# ---------------------------------------------------------------- incremental log readers
# Each file is read once, then only its new bytes on later calls, so polling every few seconds is cheap.
_FILES = {}      # path -> {"offset", "mtime", "records", "state"}


def _tail(path, parse_line, init_state):
    try:
        st = os.stat(path)
    except OSError:
        return []
    f = _FILES.get(path)
    if f is None or st.st_size < f["offset"]:
        f = _FILES[path] = {"offset": 0, "mtime": 0, "records": [], "state": init_state()}
    if st.st_mtime == f["mtime"] and st.st_size == f["offset"]:
        return f["records"]
    try:
        with open(path, "rb") as fh:
            fh.seek(f["offset"])
            chunk = fh.read()
    except OSError:
        return f["records"]
    end = chunk.rfind(b"\n") + 1          # leave a half-written last line for next time
    for raw in chunk[:end].splitlines():
        r = parse_line(raw, f["state"])
        if r:
            f["records"].append(r)
    f["offset"] += end
    f["mtime"] = st.st_mtime
    return f["records"]


def _claude_line(raw, state):
    if b'"usage"' not in raw:
        return None
    try:
        e = json.loads(raw)
    except ValueError:
        return None
    msg = e.get("message") or {}
    u = msg.get("usage")
    if not isinstance(u, dict) or msg.get("model") in (None, "<synthetic>"):
        return None
    key = (msg.get("id"), e.get("requestId"))
    seen = state.setdefault("seen", set())
    if key != (None, None):
        if key in seen:
            return None
        seen.add(key)
    t = _parse_ts(e.get("timestamp"))
    if not t:
        return None
    return {"t": t, "model": msg["model"], "src": "claude",
            "inp": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
            "cw": int(u.get("cache_creation_input_tokens") or 0), "cr": int(u.get("cache_read_input_tokens") or 0)}


def _codex_line(raw, state):
    if b'"token_count"' not in raw and b'"model"' not in raw:
        return None
    try:
        e = json.loads(raw)
    except ValueError:
        return None
    p = e.get("payload")
    if not isinstance(p, dict):
        return None
    if p.get("model"):
        state["model"] = p["model"]
    if p.get("type") != "token_count":
        return None
    t = _parse_ts(e.get("timestamp"))
    if not t:
        return None
    rl = p.get("rate_limits")
    if isinstance(rl, dict):
        state["limits"] = (t, rl)
    tot = ((p.get("info") or {}).get("total_token_usage")) or {}
    if not tot:
        return None
    prev = state.setdefault("prev", (0, 0, 0))
    cur = (int(tot.get("input_tokens", 0)), int(tot.get("cached_input_tokens", 0)), int(tot.get("output_tokens", 0)))
    state["prev"] = cur
    d_in, d_ca, d_out = cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2]
    if d_in < 0 or d_out < 0 or d_in + d_out == 0:
        return None
    return {"t": t, "model": state.get("model", "codex"), "src": "codex",
            "inp": max(0, d_in - d_ca), "cr": max(0, d_ca), "out": d_out, "cw": 0}


_ZST = {}   # compressed (archived) Codex sessions never change: parse once per mtime


def _codex_zst(path):
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return [], None
    hit = _ZST.get(path)
    if hit and hit[0] == mt:
        return hit[1], hit[2]
    try:
        import zstandard
    except ImportError:
        return None, None            # caller counts these as skipped
    state, recs = {}, []
    try:
        with open(path, "rb") as fh:
            for line in io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(fh)):
                r = _codex_line(line, state)
                if r:
                    recs.append(r)
    except Exception:
        recs = []
    _ZST[path] = (mt, recs, state.get("limits"))
    return recs, state.get("limits")


def claude_records():
    dirs = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        dirs.append(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
    dirs += [HOME / ".claude" / "projects", HOME / ".config" / "claude" / "projects"]
    out, done = [], set()
    for d in dirs:
        for p in glob.glob(str(d / "**" / "*.jsonl"), recursive=True):
            if p not in done:
                done.add(p)
                out += _tail(p, _claude_line, dict)
    # the same response can be logged in a session and in its subagent files: drop repeats across files
    seen, uniq = set(), []
    for r in out:
        k = (r["t"], r["model"], r["inp"], r["out"], r["cr"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def codex_records():
    root = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
    out, limits, skipped = [], None, 0
    for sub in ("sessions", "archived_sessions"):
        d = root / sub
        for p in glob.glob(str(d / "**" / "*.jsonl"), recursive=True):
            out += _tail(p, _codex_line, dict)
            lim = _FILES.get(p, {}).get("state", {}).get("limits")
            if lim and (limits is None or lim[0] > limits[0]):
                limits = lim
        for p in glob.glob(str(d / "**" / "*.jsonl.zst"), recursive=True):
            recs, lim = _codex_zst(p)
            if recs is None:
                skipped += 1
                continue
            out += recs
            if lim and (limits is None or lim[0] > limits[0]):
                limits = lim
    return out, limits, skipped


# ---------------------------------------------------------------- Claude plan limits (status line)
def claude_limits():
    try:
        d = json.loads(LIMITS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rl = d.get("rate_limits") or {}
    out = {"as_of": d.get("at")}
    for k in ("five_hour", "seven_day"):
        w = rl.get(k)
        if isinstance(w, dict) and w.get("used_percentage") is not None:
            out[k] = {"pct": float(w["used_percentage"]), "resets_at": w.get("resets_at")}
    return out if len(out) > 1 else {"as_of": d.get("at")}


def _ours(cmd):
    norm = lambda x: str(x).replace("\\", "/").lower()
    return norm(STATUSLINE) in norm(cmd)


def statusline_state():
    try:
        s = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"installed": False, "other": None}
    except (OSError, ValueError):
        return {"installed": False, "other": None, "error": "Couldn't read ~/.claude/settings.json"}
    cmd = (s.get("statusLine") or {}).get("command", "")
    if _ours(cmd):
        return {"installed": True, "other": None}
    return {"installed": False, "other": cmd or None}


def install_statusline():
    """Point Claude Code's status line at dashboard/statusline.py so it records your plan limits.

    An existing status line is kept: statusline.py runs it and prints its output, so nothing you see changes.
    """
    try:
        s = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        s = {}
    except ValueError:
        raise ValueError("~/.claude/settings.json isn't valid JSON, so it wasn't changed. Fix it and try again.")
    old = (s.get("statusLine") or {}).get("command", "")
    if _ours(old):
        return statusline_state()
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        py = py[:-len("pythonw.exe")] + "python.exe"
    c = config()
    if old:
        c["statusline_chain"] = old        # statusline.py runs the previous one after recording limits
        _save(c)
    if CLAUDE_SETTINGS.exists():
        backup = CLAUDE_SETTINGS.with_name("settings.json.before-aiflow")
        if not backup.exists():
            backup.write_text(CLAUDE_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    fwd = lambda x: str(x).replace("\\", "/")     # works in both cmd.exe and Git Bash
    s["statusLine"] = {"type": "command", "command": f'"{fwd(py)}" "{fwd(STATUSLINE)}"'}
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = CLAUDE_SETTINGS.with_suffix(".aiflow.tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, CLAUDE_SETTINGS)
    return statusline_state()


# ---------------------------------------------------------------- Anthropic Admin API
_API_CACHE = {}
API_TTL = 300  # the cost data lags ~5 min anyway; the docs ask for at most one poll a minute


def _anthropic_get(path, params, key):
    q = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(f"https://api.anthropic.com{path}?{q}", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "User-Agent": "AiFlow-Dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("Anthropic rejected the Admin key (check it in the Console under Settings → Admin keys).")
        raise ValueError(f"Anthropic API error {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Couldn't reach api.anthropic.com")


def api_month(key):
    """Month-to-date $ and tokens billed to the Anthropic API org (all API keys, e.g. YT_series, site chat)."""
    now = time.time()
    hit = _API_CACHE.get("month")
    if hit and now - hit["fetched"] < API_TTL:
        return hit["data"]
    start = _month_start_utc()
    cents, daily_cost, page = 0.0, {}, None
    while True:
        params = {"starting_at": start, "bucket_width": "1d", "limit": 31}
        if page:
            params["page"] = page
        d = _anthropic_get("/v1/organizations/cost_report", params, key)
        for b in d.get("data", []):
            day_c = sum(float(r.get("amount") or 0) for r in b.get("results", []))
            cents += day_c
            daily_cost[b.get("starting_at", "")[:10]] = round(day_c / 100, 4)
        page = d.get("next_page") if d.get("has_more") else None
        if not page:
            break
    tokens, page = 0, None
    while True:
        params = {"starting_at": start, "bucket_width": "1d", "limit": 31}
        if page:
            params["page"] = page
        d = _anthropic_get("/v1/organizations/usage_report/messages", params, key)
        for b in d.get("data", []):
            for r in b.get("results", []):
                cc = r.get("cache_creation") or {}
                tokens += int(r.get("uncached_input_tokens") or 0) + int(r.get("cache_read_input_tokens") or 0) \
                    + int(r.get("output_tokens") or 0) + sum(int(v or 0) for v in cc.values() if isinstance(v, (int, float)))
        page = d.get("next_page") if d.get("has_more") else None
        if not page:
            break
    data = {"cost": round(cents / 100, 2), "tokens": tokens, "daily_cost": daily_cost,
            "as_of": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    _API_CACHE["month"] = {"fetched": now, "data": data}
    return data


# ---------------------------------------------------------------- the snapshot the page polls
def status():
    with _lock:
        return _status()


def _status():
    c = config()
    now = dt.datetime.now().astimezone()
    today = now.date()
    cp, xp = c["claude_plan"], c["codex_plan"]
    c_start, c_end = _cycle(int(cp.get("billing_day", 1)), today)
    x_start, x_end = _cycle(int(xp.get("billing_day", 1)), today)

    cl = claude_records()
    cx, cx_limits, cx_skipped = codex_records() if xp.get("on", True) else ([], None, 0)

    def tok(r):
        return r["inp"] + r["out"] + r["cw"] + r["cr"]

    def cost(r):
        p = _price(r["model"])
        return (r["inp"] * p[0] + r["out"] * p[1] + r["cw"] * p[2] + r["cr"] * p[3]) / 1e6

    hour_ago = now - dt.timedelta(hours=1)
    claude = {"today": 0, "week": 0, "cycle": 0, "last_hour": 0, "api_value": 0.0, "api_value_today": 0.0,
              "models": {}, "last_at": None}
    for r in cl:
        d, n = r["t"].date(), tok(r)
        if d == today:
            claude["today"] += n
            claude["api_value_today"] += cost(r)
        if (today - d).days < 7:
            claude["week"] += n
        if c_start <= d < c_end:
            claude["cycle"] += n
            claude["api_value"] += cost(r)
            m = _short_model(r["model"])
            claude["models"][m] = claude["models"].get(m, 0) + n
        if r["t"] >= hour_ago:
            claude["last_hour"] += n
        if claude["last_at"] is None or r["t"] > claude["last_at"]:
            claude["last_at"] = r["t"]
    codex = {"today": 0, "week": 0, "cycle": 0, "last_hour": 0, "last_at": None, "model": None}
    for r in cx:
        d, n = r["t"].date(), tok(r)
        if d == today:
            codex["today"] += n
        if (today - d).days < 7:
            codex["week"] += n
        if x_start <= d < x_end:
            codex["cycle"] += n
        if r["t"] >= hour_ago:
            codex["last_hour"] += n
        if codex["last_at"] is None or r["t"] > codex["last_at"]:
            codex["last_at"], codex["model"] = r["t"], r["model"]

    by_day = {}
    for r in cl + cx:
        d = r["t"].date()
        if (today - d).days < 14:
            k = by_day.setdefault(d, {"claude": 0, "codex": 0})
            k[r["src"]] += tok(r)
    daily = []
    for i in range(13, -1, -1):
        d = today - dt.timedelta(days=i)
        daily.append({"date": d.isoformat(), **by_day.get(d, {"claude": 0, "codex": 0})})

    cx_limit = None
    if cx_limits:
        prim = cx_limits[1].get("primary") or {}
        if prim.get("used_percent") is not None:
            cx_limit = {"pct": float(prim["used_percent"]), "resets_at": prim.get("resets_at"),
                        "window_minutes": prim.get("window_minutes"), "plan_type": cx_limits[1].get("plan_type"),
                        "as_of": cx_limits[0].isoformat(timespec="seconds")}

    api = {"connected": bool(c.get("admin_key"))}
    if api["connected"]:
        try:
            api.update(api_month(c["admin_key"]))
        except ValueError as e:
            api["error"] = str(e)

    owed = [{"name": cp["name"], "amount": float(cp["monthly_fee"]), "kind": "plan"}]
    if xp.get("on", True):
        owed.append({"name": xp["name"], "amount": float(xp["monthly_fee"]), "kind": "plan"})
    if api.get("cost") is not None:
        owed.append({"name": "Anthropic API (this month)", "amount": api["cost"], "kind": "api"})

    iso = lambda t: t.isoformat(timespec="seconds") if t else None
    return {
        "updated": iso(now),
        "owed": {"total": round(sum(o["amount"] for o in owed), 2), "parts": owed},
        "tokens": {"today": claude["today"] + codex["today"], "week": claude["week"] + codex["week"],
                   "last_hour": claude["last_hour"] + codex["last_hour"],
                   "cycle": claude["cycle"] + codex["cycle"] + int(api.get("tokens") or 0)},
        "claude": {"plan": cp["name"], "fee": cp["monthly_fee"], "billing_day": cp["billing_day"],
                   "cycle_start": c_start.isoformat(), "cycle_end": c_end.isoformat(), "days_left": (c_end - today).days,
                   "today": claude["today"], "week": claude["week"], "cycle": claude["cycle"],
                   "last_hour": claude["last_hour"], "last_at": iso(claude["last_at"]),
                   "api_value": round(claude["api_value"], 2), "api_value_today": round(claude["api_value_today"], 2),
                   "models": sorted(({"name": k, "tokens": v} for k, v in claude["models"].items()), key=lambda m: -m["tokens"]),
                   "limits": claude_limits(), "statusline": statusline_state()},
        "codex": {"on": bool(xp.get("on", True)), "plan": xp["name"], "fee": xp["monthly_fee"], "billing_day": xp["billing_day"],
                  "today": codex["today"], "week": codex["week"], "cycle": codex["cycle"], "last_hour": codex["last_hour"],
                  "last_at": iso(codex["last_at"]), "model": codex["model"], "limit": cx_limit,
                  "skipped_archives": cx_skipped},
        "api": {k: v for k, v in api.items() if k != "daily_cost"},
        "daily": daily,
    }


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))

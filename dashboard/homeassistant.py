"""Home Assistant bridge: security cameras and smart devices for the dashboard and Jarvis.

The owner creates a long-lived access token in Home Assistant (Profile > Security) and pastes it into the
dashboard's Smart devices page. It is kept in tasks/homeassistant.json (gitignored), only ever sent to the
owner's own Home Assistant on the home network, and never sent back to the browser.

Safety: only the services listed in SERVICES can be called. Garage doors, locks and alarms (SENSITIVE) need
an explicit confirmation, and alarms can be armed but never disarmed from here.

  python dashboard/homeassistant.py status    -> connection check and device counts
"""
import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "tasks" / "homeassistant.json"
SHOWN = ("camera", "light", "switch", "fan", "climate", "cover", "lock", "media_player", "scene", "alarm_control_panel")
SENSITIVE = {"cover", "lock", "alarm_control_panel"}
# domain -> service -> allowed data keys
SERVICES = {
    "light": {"turn_on": {"brightness_pct", "rgb_color", "color_temp_kelvin"}, "turn_off": set(), "toggle": set()},
    "switch": {"turn_on": set(), "turn_off": set(), "toggle": set()},
    "fan": {"turn_on": {"percentage"}, "turn_off": set(), "toggle": set(), "set_percentage": {"percentage"}},
    "climate": {"set_temperature": {"temperature", "target_temp_low", "target_temp_high"},
                "set_hvac_mode": {"hvac_mode"}, "turn_on": set(), "turn_off": set()},
    "cover": {"open_cover": set(), "close_cover": set(), "stop_cover": set()},
    "lock": {"lock": set(), "unlock": set()},
    "media_player": {"media_play": set(), "media_pause": set(), "media_play_pause": set(), "volume_set": {"volume_level"},
                     "turn_on": set(), "turn_off": set()},
    "scene": {"turn_on": set()},
    "alarm_control_panel": {"alarm_arm_home": set(), "alarm_arm_away": set()},
}
ATTRS = ("brightness", "rgb_color", "supported_color_modes", "current_temperature", "temperature", "target_temp_low",
         "target_temp_high", "hvac_modes", "hvac_action", "min_temp", "max_temp", "current_position", "percentage",
         "volume_level", "media_title", "media_artist", "unit_of_measurement", "entity_picture")
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_cache = {"at": 0, "states": None}


# ---------- config ----------
def config():
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def configured():
    c = config()
    return bool(c.get("url") and c.get("token"))


def _check_url(url):
    """Only the owner's own Home Assistant on the home network, so the token can't be sent anywhere else."""
    url = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    local = host in ("homeassistant", "localhost") or host.endswith(".local")
    try:
        local = local or ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    if not host or not local or u.path not in ("", "/"):
        raise ValueError("Use your Home Assistant's address on your home network, for example http://homeassistant.local:8123")
    return f"{u.scheme}://{u.netloc}"


def setup(url, token):
    url, token = _check_url(url), str(token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._\-]{20,1000}", token):
        raise ValueError("That doesn't look like a Home Assistant access token. Copy the whole token and try again.")
    msg = _req("GET", "/api/", cfg={"url": url, "token": token}).get("message", "")
    if "running" not in msg.lower():
        raise ValueError("Home Assistant answered, but not as expected. Check the address.")
    CFG.write_text(json.dumps({"url": url, "token": token}, indent=1), encoding="utf-8")
    _cache["at"] = 0
    return status()


def disconnect():
    CFG.unlink(missing_ok=True)
    _cache.update(at=0, states=None)


# ---------- HTTP ----------
def _req(method, path, body=None, cfg=None, raw=False, timeout=8):
    c = cfg or config()
    if not (c.get("url") and c.get("token")):
        raise ValueError("Home Assistant isn't connected yet")
    req = urllib.request.Request(c["url"] + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {c['token']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return (data, r.headers.get("Content-Type", "image/jpeg")) if raw else json.loads(data or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("Home Assistant rejected the access token. Create a new one and connect again.")
        raise ValueError(f"Home Assistant returned an error ({e.code})")
    except (urllib.error.URLError, OSError):
        raise ValueError("Can't reach Home Assistant. Is the virtual machine running?")


# ---------- reading ----------
def states(fresh=False):
    if not fresh and _cache["states"] is not None and time.time() - _cache["at"] < 3:
        return _cache["states"]
    out = []
    for s in _req("GET", "/api/states"):
        eid = s.get("entity_id", "")
        domain = eid.split(".", 1)[0]
        if domain not in SHOWN:
            continue
        a = s.get("attributes") or {}
        if a.get("hidden") or a.get("entity_category"):
            continue
        out.append({"entity_id": eid, "domain": domain, "name": a.get("friendly_name") or eid.split(".", 1)[1].replace("_", " "),
                    "state": s.get("state"), "attrs": {k: a[k] for k in ATTRS if k in a and k != "entity_picture"},
                    "sensitive": domain in SENSITIVE, "changed": s.get("last_changed")})
    out.sort(key=lambda d: (SHOWN.index(d["domain"]), d["name"].lower()))
    _cache.update(at=time.time(), states=out)
    return out


def status():
    if not configured():
        return {"configured": False}
    try:
        st = states(fresh=True)
    except ValueError as e:
        return {"configured": True, "ok": False, "error": str(e), "url": config()["url"]}
    count = {}
    for d in st:
        count[d["domain"]] = count.get(d["domain"], 0) + 1
    return {"configured": True, "ok": True, "url": config()["url"], "counts": count}


def snapshot(entity_id):
    if not re.fullmatch(r"camera\.[a-z0-9_]+", entity_id or ""):
        raise ValueError("Not a camera")
    return _req("GET", f"/api/camera_proxy/{entity_id}", raw=True, timeout=12)


def summary_for_jarvis():
    """One line per device, for Jarvis's context (data only)."""
    if not configured():
        return "Home Assistant is not connected yet."
    try:
        st = states()
    except ValueError as e:
        return str(e)
    lines = []
    for d in st:
        a, extra = d["attrs"], ""
        if d["domain"] == "climate":
            extra = f" (inside {a.get('current_temperature')}°, set to {a.get('temperature') or str(a.get('target_temp_low')) + '-' + str(a.get('target_temp_high'))}°)"
        lines.append(f"- {d['name']} [{d['domain']}]: {d['state']}{extra}")
    return "\n".join(lines[:80]) or "No devices found."


# ---------- control ----------
def call(entity_id, service, data=None, confirm=False):
    if not ENTITY.fullmatch(entity_id or ""):
        raise ValueError("Unknown device")
    domain = entity_id.split(".", 1)[0]
    allowed = SERVICES.get(domain, {})
    if service not in allowed:
        raise ValueError("That action isn't allowed from the dashboard")
    data = {k: v for k, v in (data or {}).items() if k in allowed[service]}
    dev = next((d for d in states() if d["entity_id"] == entity_id), None)
    if not dev:
        raise ValueError("That device isn't in Home Assistant")
    if domain in SENSITIVE and not confirm:
        return {"needs_confirm": True, "say": f"Should I {_verb(service)} the {dev['name']}?",
                "action": {"entity_id": entity_id, "service": service, "data": data}}
    _req("POST", f"/api/services/{domain}/{service}", {"entity_id": entity_id, **data})
    _cache["at"] = 0
    return {"ok": True, "say": f"{dev['name']} activated." if domain == "scene" else _done(dev["name"], service, data)}


def _verb(service):
    return {"open_cover": "open", "close_cover": "close", "stop_cover": "stop", "lock": "lock", "unlock": "unlock",
            "alarm_arm_home": "arm (home)", "alarm_arm_away": "arm (away)"}.get(service, service.replace("_", " "))


def _done(name, service, data):
    if service == "set_temperature":
        return f"The {name} is now set to {data.get('temperature') or data.get('target_temp_low')} degrees."
    if service == "turn_on" and "brightness_pct" in data:
        return f"The {name} is at {data['brightness_pct']} percent."
    if service == "turn_on" and "rgb_color" in data:
        return f"The {name} color is changed."
    words = {"turn_on": "on", "turn_off": "off", "toggle": "toggled", "open_cover": "opening", "close_cover": "closing",
             "stop_cover": "stopped", "lock": "locked", "unlock": "unlocked", "media_play": "playing", "media_pause": "paused",
             "alarm_arm_home": "armed (home)", "alarm_arm_away": "armed (away)", "set_hvac_mode": f"in {data.get('hvac_mode', '')} mode"}
    return f"The {name} is {words.get(service, 'done')}."


# ---------- Jarvis voice commands ----------
COLORS = {"red": [255, 0, 0], "green": [0, 255, 0], "blue": [0, 0, 255], "purple": [150, 0, 255], "pink": [255, 80, 180],
          "orange": [255, 120, 0], "yellow": [255, 220, 0], "white": [255, 255, 255], "warm white": [255, 190, 120],
          "cyan": [0, 255, 255], "teal": [0, 180, 160]}
FILLER = {"the", "my", "a", "please", "to", "in", "on", "off", "of", "all", "jarvis", "can", "you", "could", "would", "turn",
          "switch", "set", "make", "it", "is", "are", "and", "light", "lights", "lamp", "percent", "degrees"}


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in FILLER]


def _match(phrase, devices):
    """Best-matching devices for a spoken name; ties return all of them (e.g. 'the porch lights')."""
    want = set(_words(phrase))
    if not want:
        return []
    scored = []
    for d in devices:
        have = set(_words(d["name"])) | set(_words(d["entity_id"].split(".", 1)[1].replace("_", " ")))
        hit = len(want & have)
        if hit:
            scored.append((hit / len(want | have) + hit, d))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [d for s, d in scored if s == best]


def command(text):
    """Understand a spoken device command. Returns {handled, say, confirm?, camera?}."""
    r = _command(text)
    if r.get("say"):
        r["say"] = re.sub(r"\b[Tt]he My ", lambda m: "Your " if m.group(0)[0] == "T" else "your ", r["say"])
    return r


def _command(text):
    if not configured():
        return {"handled": False}
    t = " ".join(text.lower().replace("’", "'").split()).rstrip(".!?")
    try:
        st = states()
    except ValueError as e:
        return {"handled": True, "say": str(e)}
    by = lambda *doms: [d for d in st if d["domain"] in doms]

    def run(dev, service, data=None):
        r = call(dev["entity_id"], service, data or {})
        if r.get("needs_confirm"):
            return {"handled": True, "say": r["say"], "confirm": r["action"]}
        return {"handled": True, "say": r["say"]}

    # cameras: "show me the front door camera"
    m = re.search(r"\b(show|open|pull up|let me see|view)\b.*?\b(cameras?|cams?|doorbell)\b", t) or re.search(r"\b(cameras?|cams?)\b", t)
    if m and by("camera"):
        name = re.sub(r"\b(show|open|pull up|let me see|view|me|camera|cam|cameras)\b", " ", t)
        cams = _match(name, by("camera")) or (by("camera") if "cameras" in t or len(by("camera")) == 1 else [])
        if cams:
            return {"handled": True, "say": f"Here's the {cams[0]['name']}." if len(cams) == 1 else "Here are your cameras.",
                    "camera": cams[0]["entity_id"] if len(cams) == 1 else "all"}

    # thermostat
    climate = by("climate")
    if climate and re.search(r"\b(thermostat|heat|heating|ac|air|cool|cooling|temperature inside|inside temperature|house temperature|degrees)\b", t):
        dev = (_match(t, climate) or climate)[0]
        n = re.search(r"\b(\d{2})\b", t)
        if n and re.search(r"\b(set|make|change|put|turn)\b", t):
            return run(dev, "set_temperature", {"temperature": int(n.group(1))})
        if re.search(r"\b(turn|switch|set|put|change)\b", t):
            modes = [(m.start(), m.group(1)) for m in re.finditer(r"\b(heat|cool|off|auto)\b", t)]
            if modes:
                mode = max(modes)[1]
                have = dev["attrs"].get("hvac_modes") or []
                mode = "heat_cool" if mode == "auto" and "heat_cool" in have else mode
                if not have or mode in have:
                    return run(dev, "set_hvac_mode", {"hvac_mode": mode})
        a = dev["attrs"]
        return {"handled": True, "say": f"It's {a.get('current_temperature')} degrees inside. The {dev['name']} is set to "
                                        f"{a.get('temperature') or a.get('target_temp_low')} and is {dev['state']}."}

    # garage / doors / locks
    if re.search(r"\b(garage|door|gate|lock|unlock|locked|unlocked)\b", t):
        if re.search(r"\b(is|are|did|check)\b", t) and not re.search(r"\b(open|close|lock|unlock) (the|my)\b", t):
            devs = _match(t, by("cover", "lock")) or by("cover", "lock")
            if devs:
                return {"handled": True, "say": " ".join(f"The {d['name']} is {d['state']}." for d in devs[:4])}
        for verb, dom, svc in (("unlock", "lock", "unlock"), ("lock", "lock", "lock"), ("open", "cover", "open_cover"),
                               ("close", "cover", "close_cover"), ("shut", "cover", "close_cover")):
            if re.search(rf"\b{verb}\b", t):
                devs = _match(re.sub(rf"\b{verb}\b", " ", t), by(dom)) or (by(dom) if len(by(dom)) == 1 else [])
                if devs:
                    return run(devs[0], svc)

    # lights, switches, fans, scenes
    if re.search(r"\b(scene|activate|run)\b", t) and by("scene"):
        sc = _match(re.sub(r"\b(scene|activate|run)\b", " ", t), by("scene"))
        if sc:
            return run(sc[0], "turn_on")
    # status questions: "are any lights on", "is the porch light on" (answered, never acted on)
    if re.match(r"(is|are|was|were|did|do|does|what|which|how many)\b", t):
        if re.search(r"\b(any|all|which|how many)\b.*\blights?\b|\bwhat'?s on\b", t):
            lit = [d["name"] for d in by("light") if d["state"] == "on"]
            return {"handled": True, "say": f"{len(lit)} light{'s are' if len(lit) != 1 else ' is'} on: {', '.join(lit[:6])}." if lit else "All the lights are off."}
        devs = _match(t, by("light", "switch", "fan", "media_player"))
        if devs:
            return {"handled": True, "say": " ".join(f"The {d['name']} is {d['state']}." for d in devs[:5])}
        return {"handled": False}
    on = re.search(r"\b(turn|switch|put)\b.*\bon\b|\bon\b$|\bturn on\b", t)
    off = re.search(r"\b(turn|switch|shut|put)\b.*\boff\b|\boff\b$|\bturn off\b", t)
    pct = re.search(r"\b(\d{1,3})\s*(%|percent)", t)
    color = next((c for c in sorted(COLORS, key=len, reverse=True) if re.search(rf"\b{c}\b", t)), None)
    if on or off or pct or (color and re.search(r"\b(make|turn|set|change)\b", t)) or re.search(r"\bdim\b|\bbrighten\b", t):
        pool = by("light", "switch", "fan")
        target = re.sub(r"\b(dim|brighten|\d{1,3}\s*(%|percent))\b", " ", t)
        if color:
            target = re.sub(rf"\b{color}\b", " ", target)
        devs = _match(target, pool)
        if not devs and re.search(r"\b(all|every|the lights|lights)\b", t):
            devs = by("light")
        if devs:
            done = []
            for d in devs[:12]:
                if pct and d["domain"] == "light":
                    r = call(d["entity_id"], "turn_on", {"brightness_pct": max(1, min(100, int(pct.group(1))))})
                elif re.search(r"\bdim\b", t) and d["domain"] == "light":
                    r = call(d["entity_id"], "turn_on", {"brightness_pct": 30})
                elif re.search(r"\bbrighten\b", t) and d["domain"] == "light":
                    r = call(d["entity_id"], "turn_on", {"brightness_pct": 100})
                elif color and d["domain"] == "light":
                    r = call(d["entity_id"], "turn_on", {"rgb_color": COLORS[color]})
                else:
                    r = call(d["entity_id"], "turn_off" if off and not on or (off and on and off.start() > on.start()) else "turn_on")
                done.append(r["say"])
            return {"handled": True, "say": done[0] if len(done) == 1 else f"Done. {len(done)} devices updated."}

    return {"handled": False}


if __name__ == "__main__":
    print(json.dumps(status(), indent=1) if sys.argv[1:2] in ([], ["status"]) else __doc__)

"""Jarvis's AI providers: Claude (Claude Code CLI), Google Gemini (free API tier) and a local model (Ollama).

The owner switches each one on or off from the dashboard's top bar and picks which one answers as Jarvis.
If that one fails, the next one that's on answers instead. Settings live in tasks/ai.json (gitignored).
The Gemini key is only ever sent to Google's API and is never returned to the browser; the local model is
only reached on this PC.

  python dashboard/models.py status   -> what's on, configured and reachable
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "tasks" / "ai.json"
NAMES = {"claude": "Claude", "gemini": "Gemini", "local": "Local AI"}
ORDER = ("claude", "gemini", "local")
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
OLLAMA = "http://127.0.0.1:11434"
DEFAULT = {"primary": "claude",
           "providers": {"claude": {"on": True}, "gemini": {"on": False, "key": "", "model": ""},
                         "local": {"on": False, "model": ""}}}


# ---------- settings ----------
def config():
    try:
        saved = json.loads(CFG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    cfg = json.loads(json.dumps(DEFAULT))
    cfg["primary"] = saved.get("primary") if saved.get("primary") in ORDER else "claude"
    for k in ORDER:
        cfg["providers"][k].update({x: v for x, v in (saved.get("providers", {}).get(k) or {}).items()
                                    if x in cfg["providers"][k]})
    return cfg


def save(cfg):
    CFG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


def update(body):
    """Owner changes from the dashboard: {provider, on?, key?, model?} and/or {primary}."""
    cfg = config()
    p = body.get("provider")
    if p is not None:
        if p not in ORDER:
            raise ValueError("Unknown AI")
        c = cfg["providers"][p]
        if "on" in body:
            c["on"] = bool(body["on"])
        if p == "gemini" and body.get("key") is not None:
            key = str(body["key"]).strip()
            if key and not re.fullmatch(r"[A-Za-z0-9_\-]{30,80}", key):
                raise ValueError("That doesn't look like a Gemini API key. Copy the whole key from Google AI Studio.")
            c["key"], c["model"] = key, ""          # a new key: pick the best free model again
            if key:
                c["on"] = True
        if p in ("gemini", "local") and body.get("model") is not None:
            c["model"] = re.sub(r"[^A-Za-z0-9._:/\-]", "", str(body["model"]))[:80]
    if body.get("primary") in ORDER:
        cfg["primary"] = body["primary"]
        cfg["providers"][body["primary"]]["on"] = True
    if not any(cfg["providers"][k]["on"] for k in ORDER):
        raise ValueError("Keep at least one AI switched on, so Jarvis can answer.")
    if not cfg["providers"][cfg["primary"]]["on"]:          # the chosen voice was switched off: hand over
        cfg["primary"] = next(k for k in ORDER if cfg["providers"][k]["on"])
    save(cfg)
    return status()


# ---------- HTTP ----------
def _post(url, body, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(url, headers=None, timeout=8):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
        return json.load(r)


def _err(e, who):
    if isinstance(e, urllib.error.HTTPError):
        try:
            msg = json.load(e).get("error", {})
            msg = msg.get("message", "") if isinstance(msg, dict) else str(msg)
        except Exception:
            msg = ""
        if e.code in (401, 403):
            return f"{who} rejected the key. Check it in AI settings."
        if e.code == 429:
            return f"{who}'s free limit is used up for now. It resets soon."
        return f"{who} returned an error ({e.code}). {msg[:120]}".strip()
    return f"Can't reach {who}." + (" Is the Ollama app running?" if who == "the local AI" else " Check the internet connection.")


# ---------- Gemini ----------
def _gemini_model(c):
    """The newest stable 'flash' model the key can use (fast, and on the free tier)."""
    if c.get("model"):
        return c["model"]
    data = _get(f"{GEMINI}/models?pageSize=200", {"x-goog-api-key": c["key"]})
    names = [m["name"].split("/", 1)[1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not re.search(r"lite|image|tts|live|audio|exp|preview|thinking|embed", n)]
    ver = lambda n: [float(x) for x in re.findall(r"\d+(?:\.\d+)?", n)[:1]] or [0]
    pick = sorted(flash, key=ver, reverse=True)[:1] or names[:1]
    if not pick:
        raise ValueError("Your Gemini key has no text models available.")
    cfg = config(); cfg["providers"]["gemini"]["model"] = pick[0]; save(cfg)
    return pick[0]


def _gemini(c, system, turns):
    model = _gemini_model(c)
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]} for t in turns],
            "generationConfig": {"temperature": .7, "maxOutputTokens": 600}}
    url, hdr = f"{GEMINI}/models/{model}:generateContent", {"x-goog-api-key": c["key"]}
    try:   # with Google Search grounding, so news and "latest" questions get current answers
        data = _post(url, {**body, "tools": [{"google_search": {}}]}, hdr)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise
        data = _post(url, body, hdr)
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise ValueError("Gemini returned an empty answer.")
    return text


# ---------- local model (Ollama) ----------
def local_models():
    return [m["name"] for m in _get(f"{OLLAMA}/api/tags", timeout=3).get("models", [])]


def _local(c, system, turns):
    have = local_models()
    if not have:
        raise ValueError("The local AI has no model yet. In a terminal run: ollama pull llama3.1:8b")
    model = c.get("model") if c.get("model") in have else have[0]
    data = _post(f"{OLLAMA}/api/chat", {"model": model, "stream": False, "options": {"temperature": .7},
                                        "messages": [{"role": "system", "content": system}] + turns}, timeout=180)
    text = (data.get("message") or {}).get("content", "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()    # some models print their reasoning
    if not text:
        raise ValueError("The local AI returned an empty answer.")
    return text


def chat(provider, system, turns):
    """One answer from Gemini or the local model. turns: [{role: user|assistant, content}], ending on user."""
    c = config()["providers"][provider]
    who = {"gemini": "Gemini", "local": "the local AI"}[provider]
    try:
        if provider == "gemini":
            if not c.get("key"):
                raise ValueError("Gemini needs a free API key. Add it in AI settings.")
            return _gemini(c, system, turns)
        return _local(c, system, turns)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ValueError(_err(e, who))


# ---------- status ----------
def status():
    cfg = config(); out = {"primary": cfg["primary"], "providers": {}}
    for k in ORDER:
        c = cfg["providers"][k]; s = {"name": NAMES[k], "on": c["on"]}
        if k == "claude":
            import dispatch
            s["ready"] = bool(dispatch.cli_path())
            s["note"] = "Claude Code on this PC" if s["ready"] else "Claude Code CLI not found"
        elif k == "gemini":
            s["ready"] = bool(c.get("key"))
            s["key"] = ("•••• " + c["key"][-4:]) if c.get("key") else ""
            s["model"] = c.get("model") or ""
            s["note"] = f"Free tier · {s['model'] or 'auto'}" if s["ready"] else "Needs a free key from Google AI Studio"
        else:
            try:
                have = local_models()
                s["ready"] = bool(have)
                s["models"] = have
                s["model"] = c.get("model") if c.get("model") in have else (have[0] if have else "")
                s["note"] = f"On this PC · {s['model']}" if have else "Ollama is running but has no model yet"
            except (urllib.error.URLError, OSError, TimeoutError, ValueError):
                s["ready"], s["models"], s["model"] = False, [], ""
                s["note"] = "Install Ollama to use a free AI on this PC"
        out["providers"][k] = s
    return out


def route():
    """Who answers, in order: the chosen one first, then the other providers that are switched on."""
    cfg = config()
    first = cfg["primary"]
    return [first] + [k for k in ORDER if k != first and cfg["providers"][k]["on"]]


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    print(json.dumps(status(), indent=1))

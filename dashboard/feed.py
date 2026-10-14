"""Turns Claude Code session transcripts of scheduled workflow runs into a plain-English live feed.

Transcripts live in ~/.claude/projects/<encoded project path>/<session>.jsonl, with helper agents in
<session>/subagents/*.jsonl. Only sessions started by a scheduled task are shown.
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = Path.home() / ".claude" / "projects"
MAX_AGE_H = 36
MAX_EVENTS = 400
LIVE_SECONDS = 120

RUN_NAMES = {"ai-brief": "Morning brief", "ai-queue": "Queue worker", "ai-evening-digest": "Evening report",
             "ai-manager": "Manager plan", "ai-approvals": "Approved deploys", "ai-phone-sync": "Phone sync",
             "ai-morning-brief": "Morning brief (old)", "ai-queue-worker": "Queue worker (old)"}
HIDE_TOOLS = {"ToolSearch", "TodoWrite", "TaskOutput", "Monitor"}

_cache = {}  # path -> {"mtime", "size", "offset", "events", "meta"}


def project_dir():
    # Claude Code encodes the project path by replacing every non-alphanumeric char with '-'.
    return PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))


def short_path(p):
    p = str(p or "").replace("\\", "/")
    root = str(ROOT).replace("\\", "/")
    if p.lower().startswith(root.lower()):
        p = p[len(root):].lstrip("/") or "workflow folder"
    return p if len(p) < 90 else "…" + p[-88:]


def clip(s, n=220):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_tool(name, inp):
    """Return (kind, text, detail) for a tool call, or None to hide it."""
    if name in HIDE_TOOLS:
        return None
    if name == "WebSearch":
        return "search", f"Searching the web for “{clip(inp.get('query'), 140)}”", ""
    if name == "WebFetch":
        u = urlparse(inp.get("url", ""))
        return "web", f"Reading {u.netloc}{clip(u.path, 70) if u.path not in ('', '/') else ''}", clip(inp.get("prompt"), 200) and "Looking for: " + clip(inp.get("prompt"), 200)
    if name == "Read":
        return "read", f"Opening {short_path(inp.get('file_path'))}", ""
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return "edit", f"Editing {short_path(inp.get('file_path') or inp.get('notebook_path'))}", ""
    if name == "Write":
        return "write", f"Writing {short_path(inp.get('file_path'))}", ""
    if name in ("Glob", "Grep"):
        return "find", f"Looking through files for “{clip(inp.get('pattern'), 100)}”", ""
    if name in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        if "progress.py" in cmd:
            m = re.search(r"step .*?--step\s+(\d+)", cmd)
            note = re.search(r'--note\s+"([^"]*)"', cmd)
            if m:
                return "milestone", f"Reached step {m.group(1)}" + (f": {note.group(1)}" if note and note.group(1) else ""), ""
            if re.search(r"progress\.py\s+done", cmd):
                return "milestone", "Finished a task" + (" (blocked)" if "--blocked" in cmd else ""), ""
            return None
        desc = inp.get("description")
        return "run", (desc and f"Running: {clip(desc, 140)}") or f"Running a command", clip(cmd, 160)
    if name == "Agent" or name == "Task":
        return "agent", f"Handing off to a helper: {clip(inp.get('description'), 120)}", ""
    if name == "Skill":
        return "skill", f"Following the {inp.get('skill', '')} playbook", ""
    if name.startswith("mcp__"):
        return "tool", f"Using {name.split('__')[-1].replace('_', ' ')}", ""
    return "tool", f"Using {name}", ""


def parse_lines(lines, actor, events, meta):
    for line in lines:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, ts = o.get("type"), o.get("timestamp")
        if t not in ("user", "assistant") or not ts:
            continue
        content = o.get("message", {}).get("content")
        if t == "user":
            if isinstance(content, str):
                if "first_prompt" not in meta:
                    meta["first_prompt"] = content[:4000]
                    meta["started"] = ts
                continue
            for b in content or []:
                if b.get("type") == "tool_result" and b.get("is_error"):
                    txt = b.get("content")
                    if isinstance(txt, list):
                        txt = " ".join(x.get("text", "") for x in txt if isinstance(x, dict))
                    events.append({"ts": ts, "actor": actor, "kind": "error", "text": "A step didn't work; Claude will adjust", "detail": clip(txt, 200)})
                elif b.get("type") == "text" and "first_prompt" not in meta:
                    meta["first_prompt"] = b.get("text", "")[:4000]
                    meta["started"] = ts
            continue
        for b in content or []:
            if b.get("type") == "text" and b.get("text", "").strip():
                txt = b["text"].strip()
                events.append({"ts": ts, "actor": actor, "kind": "say", "detail": "",
                               "text": txt if len(txt) <= 1500 else txt[:1499] + "…"})
            elif b.get("type") == "tool_use":
                d = describe_tool(b.get("name", ""), b.get("input") or {})
                if d:
                    if b.get("name") in ("Agent", "Task"):
                        meta.setdefault("agents", []).append({"prompt": str((b.get("input") or {}).get("prompt", ""))[:300],
                                                             "desc": (b.get("input") or {}).get("description", "helper")})
                    events.append({"ts": ts, "actor": actor, "kind": d[0], "text": d[1], "detail": d[2]})
        meta["last"] = ts


def load_file(path, actor):
    st = path.stat()
    c = _cache.get(path)
    if c and c["mtime"] == st.st_mtime and c["size"] == st.st_size:
        return c
    if not c or st.st_size < c["offset"]:
        c = {"offset": 0, "events": [], "meta": {}}
    with open(path, "rb") as f:
        f.seek(c["offset"])
        chunk = f.read()
    cut = chunk.rfind(b"\n") + 1  # only complete lines; the rest is still being written
    parse_lines(chunk[:cut].decode("utf-8", "replace").splitlines(), actor, c["events"], c["meta"])
    c.update(offset=c["offset"] + cut, mtime=st.st_mtime, size=st.st_size)
    c["events"] = c["events"][-MAX_EVENTS:]
    _cache[path] = c
    return c


def runs():
    """Recent scheduled-run sessions, newest first, without events."""
    d = project_dir()
    if not d.exists():
        return []
    out = []
    cutoff = time.time() - MAX_AGE_H * 3600
    for p in d.glob("*.jsonl"):
        if p.stat().st_mtime < cutoff:
            continue
        c = load_file(p, "main")
        fp = c["meta"].get("first_prompt", "")
        m = re.search(r'<scheduled-task name="([^"]+)"', fp)
        if not m:  # headless CLI runs started by dashboard/dispatch.py
            m = re.search(r'<ai-workflow-run name="([^"]+)"', fp)
            if m:
                m = re.match(r"(.*)", "ai-" + m.group(1))
        if not m:
            continue
        live = time.time() - p.stat().st_mtime < LIVE_SECONDS
        out.append({"id": p.stem, "task": m.group(1), "name": RUN_NAMES.get(m.group(1), m.group(1)),
                    "started": c["meta"].get("started"), "last": c["meta"].get("last"), "live": live})
    out.sort(key=lambda r: r["started"] or "", reverse=True)
    return out


def feed(session_id):
    p = project_dir() / f"{session_id}.jsonl"
    if not re.fullmatch(r"[\w-]+", session_id or "") or not p.exists():
        raise ValueError("unknown run")
    main = load_file(p, "main")
    events = list(main["events"])
    agents = main["meta"].get("agents", [])
    sub = p.with_suffix("") / "subagents"
    if sub.exists():
        for i, sp in enumerate(sorted(sub.glob("*.jsonl"))):
            c = load_file(sp, sp.stem)
            fp = c["meta"].get("first_prompt", "")
            name = next((a["desc"] for a in agents if a["prompt"] and fp.startswith(a["prompt"][:200])), f"Helper {i + 1}")
            events += [dict(e, actor=name) for e in c["events"]]
    events.sort(key=lambda e: e["ts"])
    events = events[-MAX_EVENTS:]
    live = time.time() - max([p.stat().st_mtime] + [x.stat().st_mtime for x in (sub.glob("*.jsonl") if sub.exists() else [])]) < LIVE_SECONDS
    return {"id": session_id, "live": live, "events": events}

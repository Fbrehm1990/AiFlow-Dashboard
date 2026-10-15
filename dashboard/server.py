"""Local dashboard for the AI Workflow. Run: python dashboard/server.py  (opens http://127.0.0.1:8765)

Stdlib only. Binds to 127.0.0.1 and rejects foreign Host headers and cross-site writes.
"""
import html
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feed  # noqa: E402

PORT = int(os.environ.get("AIWF_PORT", "8765"))
ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
INBOX = ROOT / "tasks" / "inbox.md"
DONE = ROOT / "tasks" / "done.md"
RECURRING = ROOT / "tasks" / "recurring.md"
GOALS = ROOT / "tasks" / "goals.md"
APPROVALS = ROOT / "tasks" / "approvals.json"
HISTORY = ROOT / "tasks" / "history.jsonl"
STATUS = ROOT / "tasks" / "status.json"
READABLE = [ROOT / "outputs", ROOT / "logs", ROOT / "tasks"]
OPENABLE_EXT = {".md", ".txt", ".csv", ".docx", ".xlsx", ".pptx", ".pdf", ".html", ".png", ".jpg", ".jpeg", ".patch", ".json"}
TYPES = ["auto", "research", "web", "marketing", "code", "brief", "docs", "plan"]  # "auto": the team routes it
PRIORITIES = ["high", "normal", "low"]

# Mirrors the schedules in the Claude app (sidebar → Scheduled). Edit here if you change them there.
SCHEDULES = [
    {"id": "ai-brief", "name": "Morning brief", "days": [0, 1, 2, 3, 4], "times": ["07:00"]},
    {"id": "ai-manager", "name": "Manager plan", "days": [0, 1, 2, 3, 4, 5, 6], "times": ["08:33"]},
    {"id": "ai-approvals", "name": "Approved deploys", "days": [0, 1, 2, 3, 4, 5, 6], "times": ["10:30", "13:30", "16:30", "19:30"]},
    {"id": "ai-phone-sync", "name": "Dispatch check", "days": [0, 1, 2, 3, 4, 5, 6],
     "times": [f"{h:02d}:{m:02d}" for h in range(7, 23) for m in (0, 30)],
     "note": "Every 30 min, 7 AM–11 PM: starts the team when tasks are waiting, syncs the phone"},
    {"id": "ai-queue", "name": "Queue worker", "days": [0, 1, 2, 3, 4, 5, 6], "times": [], "event": True,
     "note": "Starts automatically when tasks are waiting"},
    {"id": "ai-evening-digest", "name": "Evening digest", "days": [0, 1, 2, 3, 4, 5, 6], "times": ["18:06"]},
]

TASK_RE = re.compile(r"^- \[( |~)\]\s*(?:\[(\w+)\])?\s*(?:\((high|normal|low)\))?\s*(.*)$")
DONE_RE = re.compile(r"^- \[(x|!|-)\]\s*(\d{4}-\d\d-\d\d)?\s*(\d\d:\d\d)?\s*(?:\[(\w+)\])?\s*(.*)$")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8", newline="\n")


def queue_bounds(lines):
    """Index of the first line after '## Queue' (tasks live below it)."""
    for i, ln in enumerate(lines):
        if ln.strip().lower() == "## queue":
            return i + 1
    return len(lines)


def parse_inbox():
    lines = read(INBOX).splitlines()
    tasks, cur = [], None
    for i in range(queue_bounds(lines), len(lines)):
        ln = lines[i]
        m = TASK_RE.match(ln)
        if m:
            state, typ, prio, text = m.groups()
            title, _, details = text.partition(" — ")
            cur = {"line": i, "raw": ln, "state": "running" if state == "~" else "queued",
                   "type": (typ or "other").lower(), "priority": prio or "normal",
                   "title": title.strip(), "details": details.strip()}
            tasks.append(cur)
        elif cur and ln.startswith((" ", "\t")) and ln.strip():
            cur["details"] = (cur["details"] + "\n" + ln.strip()).strip()
        else:
            cur = None
    return tasks


def parse_done():
    items = []
    for ln in read(DONE).splitlines():
        m = DONE_RE.match(ln)
        if not m:
            continue
        mark, date, time, typ, rest = m.groups()
        title, _, out = rest.partition("→")
        reason = ""
        if "BLOCKED" in rest:
            head, _, reason = rest.partition("BLOCKED")
            reason = reason.lstrip(":— -").strip()
            if not out:
                title = head.rstrip(" —-.")
        status = "dismissed" if mark == "-" else "blocked" if (mark == "!" or "BLOCKED" in rest) else "done"
        items.append({"raw": ln, "status": status, "date": date or "", "time": time or "", "reason": reason,
                      "type": (typ or "other").lower(), "title": title.strip(), "output": out.strip()})
    return items


def list_outputs():
    out = []
    base = ROOT / "outputs"
    for p in base.rglob("*"):
        if not p.is_file() or p.name.startswith("."):  # skip .gitkeep and other hidden files
            continue
        if p.relative_to(base).parts[0] == "sites":  # demo sites are listed by list_sites()
            continue
        rel = p.relative_to(ROOT).as_posix()
        title = p.stem
        if p.suffix == ".md":
            for ln in read(p).splitlines()[:15]:
                if ln.startswith("# "):
                    title = ln[2:].strip()
                    break
        st = p.stat()
        out.append({"path": rel, "kind": p.relative_to(base).parts[0] if len(p.relative_to(base).parts) > 1 else "other",
                    "name": p.name, "title": title, "ext": p.suffix.lower(), "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")})
    out.sort(key=lambda o: o["modified"], reverse=True)
    return out


def list_sites():
    """Local demo sites: outputs/sites/<slug>/index.html, with the Web builder's summary if one matches."""
    base = ROOT / "outputs" / "sites"
    sites = []
    for idx in base.glob("*/index.html"):
        slug = idx.parent.name
        m = re.search(r"<title>(.*?)</title>", read(idx)[:4000], re.S | re.I)
        summary = next(iter(sorted((ROOT / "outputs" / "web").glob(f"*{slug}*.md"), reverse=True)), None)
        sites.append({"slug": slug, "title": html.unescape(" ".join((m.group(1) if m else slug).split())),
                      "path": idx.relative_to(ROOT).as_posix(),
                      "summary": summary.relative_to(ROOT).as_posix() if summary else "",
                      "modified": datetime.fromtimestamp(max(f.stat().st_mtime for f in idx.parent.rglob("*") if f.is_file())).isoformat(timespec="minutes")})
    sites.sort(key=lambda s: s["modified"], reverse=True)
    return sites


def list_logs():
    logs = sorted((ROOT / "logs").glob("*.md"), reverse=True)
    return [{"path": p.relative_to(ROOT).as_posix(), "date": p.stem} for p in logs]


def read_status():
    try:
        return json.loads(read(STATUS) or "null")
    except json.JSONDecodeError:  # mid-write; the next poll will catch it
        return None


def state():
    tasks, done = parse_inbox(), parse_done()
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "tasks": tasks,
        "done": done,
        "outputs": list_outputs(),
        "logs": list_logs(),
        "schedules": SCHEDULES,
        "recurring": read(RECURRING),
        "goals": read(GOALS),
        "sites": list_sites(),
        "approvals": read_approvals(),
        "progress": read_status(),
        "history": read_history(),
        "stats": {
            "queued": sum(t["state"] == "queued" for t in tasks),
            "running": sum(t["state"] == "running" for t in tasks),
            "doneToday": sum(d["date"] == today and d["status"] == "done" for d in done),
            "blocked": sum(d["status"] == "blocked" for d in done),
        },
    }


def safe_path(rel: str) -> Path:
    p = (ROOT / rel).resolve()
    if not any(p.is_relative_to(r.resolve()) for r in READABLE) or not p.is_file():
        raise ValueError("path not allowed")
    return p


def one_line(s: str) -> str:
    return " ".join(str(s or "").split())


def add_task(body):
    typ = body.get("type")
    prio = body.get("priority", "normal")
    title = one_line(body.get("title"))
    if typ not in TYPES or prio not in PRIORITIES or not title:
        raise ValueError("type, priority and title are required")
    summary = one_line(body.get("summary"))
    repo = one_line(body.get("repo"))
    if typ == "code" and repo:
        summary = (summary + "; " if summary else "") + "repo: " + repo
    line = f"- [ ] [{typ}] ({prio}) {title}" + (f" — {summary}" if summary else "")
    extra = [f"  {one_line(x)}" for x in str(body.get("details") or "").splitlines() if x.strip()]
    lines = read(INBOX).splitlines()
    start = queue_bounds(lines)
    if start == len(lines) and not any(l.strip().lower() == "## queue" for l in lines):
        lines += ["", "## Queue"]
        start = len(lines)
    # Insert after the last task block so the queue keeps its order.
    end = start
    for i in range(start, len(lines)):
        if lines[i].strip():
            end = i + 1
    lines[end:end] = [line, *extra]
    write(INBOX, "\n".join(lines) + "\n")
    record_handoff(typ, title, summary, str(body.get("details") or ""), body.get("project"))


def record_handoff(typ, title, summary, details, project_override=None):
    """Append who sent a task to whom to tasks/history.jsonl, for the crew hand-off trails and the timeline."""
    if summary.startswith("your feedback"):
        origin, project = "You (feedback)", project_override or title
    elif summary.startswith("step of your task"):
        origin = "Manager"
        first = details.splitlines()[0] if details else ""
        project = first.split("Part of your request:", 1)[1].strip() if "Part of your request:" in first else title
    elif summary.startswith("from Manager"):
        origin, project = "Manager", title
    elif summary.startswith("added from phone"):
        origin, project = "You (phone)", title
    else:
        origin, project = "You", title
    ev = {"ts": datetime.now().isoformat(timespec="seconds"), "event": "added", "title": title,
          "type": typ, "from": origin, "project": project}
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")


def give_feedback(body):
    """Owner feedback on one task, sent from a team member's box. Not started yet: the feedback is added to the
    task itself. Otherwise (running, done, blocked): a high-priority revision task goes to the same agent."""
    title, text = one_line(body.get("title")), str(body.get("feedback") or "").strip()
    if not title or not text:
        raise ValueError("Write some feedback first")
    lines = read(INBOX).splitlines()
    task = next((t for t in parse_inbox() if t["title"] == title), None)
    if task and task["state"] == "queued":
        i = task["line"]
        lines[block_end(lines, i):block_end(lines, i)] = [f"  Owner feedback: {one_line(text)}"]
        write(INBOX, "\n".join(lines) + "\n")
        return "added"
    done = next((d for d in parse_done() if d["title"] == title), None)
    typ = (task or done or {}).get("type") or body.get("type") or "auto"
    if typ not in TYPES:
        typ = "auto"
    hist = {h["title"]: h for h in read_history()}
    project = (hist.get(title) or {}).get("project") or title
    details = [f"Original task: {title}"]
    if done and done.get("output"):
        details.append(f"Original result: {done['output']}")
    if done and done.get("reason"):
        details.append(f"It was blocked because: {done['reason']}")
    if task:
        details.append("The original was still in progress when this feedback arrived; build on its result.")
    details += [f"Owner feedback: {line}" for line in text.splitlines() if line.strip()]
    add_task({"type": typ, "priority": "high", "title": f"Revise: {title}"[:300], "summary": "your feedback",
              "details": "\n".join(details), "project": project})
    return "revision"


VOICE_MODEL = os.environ.get("AIWF_VOICE_MODEL", "haiku")   # fast model for snappy spoken replies


def ask_assistant(body):
    """One turn of a spoken conversation, answered by Claude Code (headless) in the workflow folder.
    Returns the reply text and the session id to continue the same conversation."""
    import subprocess
    import dispatch
    text = str(body.get("text") or "").strip()[:2000]
    if not text:
        raise ValueError("I didn't catch that")
    exe = dispatch.cli_path()
    if not exe:
        raise ValueError("The Claude Code CLI isn't installed")
    address = one_line(body.get("address") or "sir")[:30]
    persona = (HERE / "prompts" / "assistant.md").read_text(encoding="utf-8").replace("{address}", address)
    cmd = [exe, "-p", text, "--output-format", "json", "--model", VOICE_MODEL,
           "--append-system-prompt", persona, "--disallowedTools", "Agent"]
    session = str(body.get("session") or "")
    if re.fullmatch(r"[0-9a-f-]{36}", session):
        cmd += ["--resume", session]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                             timeout=150, stdin=subprocess.DEVNULL, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise ValueError("That took too long. Try asking again more simply.")
    try:
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise ValueError("Claude didn't answer. Check that the CLI is signed in (claude auth login).")
    if data.get("is_error"):
        raise ValueError(str(data.get("result") or "Claude couldn't answer that.")[:300])
    return {"reply": str(data.get("result") or "").strip(), "session": data.get("session_id", "")}


def read_history(limit=400):
    out = []
    for ln in read(HISTORY).splitlines()[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


def block_end(lines, i):
    """Index just past task i and its indented detail lines."""
    j = i + 1
    while j < len(lines) and lines[j].startswith((" ", "\t")) and lines[j].strip():
        j += 1
    return j


def edit_task(body, action):
    lines = read(INBOX).splitlines()
    i, raw = int(body.get("line", -1)), body.get("raw")
    if not (queue_bounds(lines) <= i < len(lines)) or lines[i] != raw:
        raise ValueError("Inbox changed since you loaded it; refresh and try again")
    if action == "delete":
        del lines[i:block_end(lines, i)]
    elif action == "edit":
        m = TASK_RE.match(lines[i])
        state_, typ, prio, _ = m.groups()
        title, summary = one_line(body.get("title")), one_line(body.get("summary"))
        if not title:
            raise ValueError("title is required")
        new = f"- [{state_}] " + (f"[{typ}] " if typ else "") + f"({prio or 'normal'}) {title}" + (f" — {summary}" if summary else "")
        extra = [f"  {one_line(x)}" for x in str(body.get("details") or "").splitlines() if x.strip()]
        lines[i:block_end(lines, i)] = [new, *extra]
    elif action == "move":
        # Swap this task (with its detail lines) and another task, given by "with" + "withRaw".
        j = int(body.get("with", -1))
        if not (queue_bounds(lines) <= j < len(lines)) or lines[j] != body.get("withRaw") or not TASK_RE.match(lines[j]) or j == i:
            raise ValueError("Inbox changed since you loaded it; refresh and try again")
        a, b = sorted((i, j))
        blk_a, blk_b = lines[a:block_end(lines, a)], lines[b:block_end(lines, b)]
        lines[a:block_end(lines, b)] = blk_b + lines[a + len(blk_a):b] + blk_a
    elif action == "reset":
        lines[i] = lines[i].replace("- [~]", "- [ ]", 1)
    elif action == "priority":
        prio = body.get("priority")
        if prio not in PRIORITIES:
            raise ValueError("bad priority")
        m = TASK_RE.match(lines[i])
        state_, typ, _, text = m.groups()
        lines[i] = f"- [{state_}] " + (f"[{typ}] " if typ else "") + f"({prio}) {text}"
    write(INBOX, "\n".join(lines) + "\n")


def kick_dispatch():
    """Start the team right away if work is waiting (needs the Claude Code CLI; otherwise the 30-min check does it)."""
    import threading

    def go():
        try:
            import dispatch
            state, why = dispatch.start()
            print(f"dispatch: {state}: {why}", flush=True)
        except Exception as e:  # never let a failed launch break the dashboard
            print(f"dispatch failed: {e}", flush=True)
    threading.Thread(target=go, daemon=True).start()


def read_approvals():
    try:
        return json.loads(read(APPROVALS) or '{"items": []}').get("items", [])
    except json.JSONDecodeError:
        return []


def decide_approval(body):
    """Owner's decision on an agent's request. Only the dashboard (the owner) can approve or reject."""
    decision = body.get("decision")
    if decision not in ("approved", "rejected", "pending"):
        raise ValueError("decision must be approved, rejected or pending")
    import team  # shares the approvals file lock with the agents' helper
    with team.Locked():
        data = team.load()
        item = next((i for i in data["items"] if i["id"] == body.get("id")), None)
        if not item:
            raise ValueError("That request no longer exists; refresh and try again")
        if item["status"] not in ("pending", "approved", "rejected"):
            raise ValueError("That request has already been carried out")
        item.update(status=decision, note=one_line(body.get("note", "")),
                    decided=datetime.now().isoformat(timespec="seconds") if decision != "pending" else None)
        team.save(data)


def dismiss_done(raw):
    """Mark a blocked entry in done.md as dismissed ([-]); the line itself stays as history."""
    lines = read(DONE).splitlines()
    if raw not in lines or not re.match(r"^- \[(x|!)\]", raw):
        raise ValueError("History changed since you loaded it; refresh and try again")
    k = lines.index(raw)
    lines[k] = "- [-]" + raw[5:]
    write(DONE, "\n".join(lines) + "\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok_host(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._send(200, state())
            if u.path == "/api/runs":
                return self._send(200, feed.runs())
            if u.path == "/api/feed":
                return self._send(200, feed.feed(parse_qs(u.query).get("id", [""])[0]))
            if u.path == "/api/file":
                p = safe_path(parse_qs(u.query).get("path", [""])[0])
                if p.suffix.lower() not in {".md", ".txt", ".csv", ".patch", ".json"}:
                    return self._send(200, {"binary": True, "name": p.name})
                return self._send(200, {"name": p.name, "content": read(p)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # The custom header forces a CORS preflight that we never approve, so other sites can't post here.
        if not self._ok_host() or self.headers.get("X-AIWF") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(min(n, 200_000)) or b"{}")
            route = urlparse(self.path).path
            if route == "/api/tasks":
                add_task(body)
                kick_dispatch()
            elif route in ("/api/tasks/delete", "/api/tasks/reset", "/api/tasks/priority", "/api/tasks/edit", "/api/tasks/move"):
                edit_task(body, route.rsplit("/", 1)[1])
            elif route == "/api/done/dismiss":
                dismiss_done(body.get("raw", ""))
            elif route == "/api/ask":
                return self._send(200, ask_assistant(body))
            elif route == "/api/feedback":
                result = give_feedback(body)
                kick_dispatch()
                return self._send(200, {"ok": True, "result": result})
            elif route == "/api/goals":
                write(GOALS, str(body.get("text", "")).replace("\r\n", "\n"))
            elif route == "/api/approvals/decide":
                decide_approval(body)
            elif route == "/api/recurring":
                write(RECURRING, str(body.get("text", "")).replace("\r\n", "\n"))
            elif route == "/api/open":
                rel = body.get("path") or ""
                p = (ROOT / rel).resolve() if rel.endswith("/") else safe_path(rel)
                if rel.endswith("/"):
                    if not p.is_relative_to(ROOT) or not p.is_dir():
                        raise ValueError("path not allowed")
                elif p.suffix.lower() not in OPENABLE_EXT:
                    raise ValueError("file type not allowed")
                os.startfile(str(p))  # Windows: open with the default app / Explorer
            else:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"ok": True})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})


def seed_task_files():
    """First start on a fresh clone: create the personal task files from the committed examples."""
    for p in (INBOX, DONE, RECURRING, GOALS):
        ex = p.with_name(p.stem + ".example.md")
        if not p.exists() and ex.exists():
            write(p, read(ex))


if __name__ == "__main__":
    seed_task_files()
    url = f"http://127.0.0.1:{PORT}"
    # Windows lets a second process bind the same port when SO_REUSEADDR is on (Python's default here),
    # which left stale copies serving old code. Refuse instead, so "already running" is detected.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Dashboard already running at {url}")
        webbrowser.open(url)
        sys.exit(0)
    print(f"AI Workflow dashboard: {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

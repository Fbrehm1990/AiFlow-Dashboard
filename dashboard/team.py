"""Team helper: how agents add tasks and approval requests without hand-editing shared files.

AGENTS USE THIS FORM (no free text on the command line):
  1. Write tasks/requests/<short-name>.json with the Write tool, e.g.
     {"action": "add-task", "by": "Manager", "type": "web", "priority": "high", "title": "...", "details": "..."}
     {"action": "approval-add", "agent": "Marketer", "kind": "post", "platform": "Facebook", "title": "...", "file": "outputs/marketing/x.md", "summary": "..."}
  2. python dashboard/team.py submit tasks/requests/<short-name>.json

Direct forms (for people at a terminal):
  python dashboard/team.py add-task --by Manager --type web --priority normal --title "..." [--details "..."]
  python dashboard/team.py approval-add --agent Marketer --kind post --platform Facebook --title "..." --file outputs/marketing/x.md
        [--summary "..."] [--action "..."] [--spend "$10/day for 7 days"] [--site-dir "C:\\...\\site"] [--site-name my-site]
  python dashboard/team.py approval-list [--status approved] [--kind deploy]
  python dashboard/team.py approval-set ID --status done|failed [--result "..."]

Approvals live in tasks/approvals.json. Only the owner approves or rejects (through the dashboard).
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

ROOT = server.ROOT
APPROVALS = ROOT / "tasks" / "approvals.json"
LOCK = APPROVALS.with_suffix(".lock")
AGENTS = ["Manager", "Researcher", "Web builder", "Marketer"]
KINDS = ["post", "ad", "email", "deploy", "other"]
# Limits on the Manager's autonomy: enough to keep the team busy, not enough to run up usage.
MANAGER_MAX_OPEN = 8
MANAGER_MAX_PER_DAY = 6


class Locked:
    def __enter__(self):
        for _ in range(100):
            try:
                os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL))
                return self
            except FileExistsError:
                if time.time() - LOCK.stat().st_mtime > 10:
                    LOCK.unlink(missing_ok=True)
                time.sleep(0.1)
        raise SystemExit("team: could not lock approvals file")

    def __exit__(self, *a):
        LOCK.unlink(missing_ok=True)


def load():
    try:
        return json.loads(APPROVALS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": []}


def save(data):
    tmp = APPROVALS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, APPROVALS)


def now():
    return datetime.now().isoformat(timespec="seconds")


def add_task(a):
    if a.by == "Manager":
        tasks = server.parse_inbox()
        mine = [t for t in tasks if "from Manager" in t["details"]]
        if len(mine) >= MANAGER_MAX_OPEN:
            raise SystemExit(f"team: the Manager already has {len(mine)} open tasks (limit {MANAGER_MAX_OPEN}); wait for some to finish")
        today = datetime.now().strftime("%Y-%m-%d")
        log = (ROOT / "logs" / f"{today}.md")
        added_today = server.read(log).count("[manager-added-task]")
        if added_today >= MANAGER_MAX_PER_DAY:
            raise SystemExit(f"team: the Manager has added {added_today} tasks today (limit {MANAGER_MAX_PER_DAY})")
    server.add_task({"type": a.type, "priority": a.priority, "title": a.title,
                     "summary": f"from {a.by}", "details": a.details or ""})
    if a.by == "Manager":
        log = ROOT / "logs" / f"{datetime.now():%Y-%m-%d}.md"
        log.parent.mkdir(exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"- {datetime.now():%H:%M} [manager-added-task] [{a.type}] {server.one_line(a.title)}\n")
    print(json.dumps({"added": a.title, "type": a.type, "by": a.by}))


def approval_add(a):
    if a.kind == "deploy" and not (a.site_dir and a.site_name):
        raise SystemExit("team: a deploy approval needs --site-dir and --site-name")
    with Locked():
        data = load()
        n = sum(1 for i in data["items"] if i["id"].startswith(f"A{datetime.now():%Y%m%d}")) + 1
        item = {"id": f"A{datetime.now():%Y%m%d}-{n}", "created": now(), "agent": a.agent, "kind": a.kind,
                "platform": a.platform or "", "title": server.one_line(a.title), "summary": (a.summary or "").strip(),
                "file": a.file or "", "action": a.action or "", "spend": a.spend or "",
                "site_dir": a.site_dir or "", "site_name": a.site_name or "",
                "status": "pending", "decided": None, "note": "", "result": ""}
        data["items"].insert(0, item)
        save(data)
    print(json.dumps({"approval": item["id"], "status": "pending"}))


def approval_list(a):
    items = [i for i in load()["items"] if (not a.status or i["status"] == a.status) and (not a.kind or i["kind"] == a.kind)]
    print(json.dumps(items, indent=1))


def approval_set(a):
    if a.status not in ("done", "failed"):
        raise SystemExit("team: agents may only mark approved items done or failed")
    with Locked():
        data = load()
        item = next((i for i in data["items"] if i["id"] == a.id), None)
        if not item or item["status"] != "approved":
            raise SystemExit("team: only an item the owner approved can be marked done or failed")
        item.update(status=a.status, result=server.one_line(a.result or ""), finished=now())
        save(data)
    print(json.dumps({"approval": a.id, "status": a.status}))


REQUESTS = ROOT / "tasks" / "requests"


def submit(a):
    """Run a request an agent wrote as JSON with the Write tool, so command lines never carry free text
    (quotes, apostrophes or parentheses in a command can trigger a permission prompt that freezes an unattended run).

    {"action": "add-task", "by": "Manager", "type": "web", "priority": "high", "title": "...", "details": "..."}
    {"action": "approval-add", "agent": "Marketer", "kind": "post", "platform": "Facebook", "title": "...", "file": "...", ...}
    """
    path = Path(a.file)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(REQUESTS.resolve()):
        raise SystemExit("team: request files must be in tasks/requests/")
    req = json.loads(path.read_text(encoding="utf-8"))
    action = req.pop("action", None)
    ns = argparse.Namespace(**{k.replace("-", "_"): v for k, v in req.items()})
    if action == "add-task":
        for k, d in (("priority", "normal"), ("details", "")):
            setattr(ns, k, getattr(ns, k, None) or d)
        if ns.by not in AGENTS or ns.type not in server.TYPES or ns.priority not in server.PRIORITIES or not getattr(ns, "title", ""):
            raise SystemExit("team: add-task needs by, type, title (and a valid priority)")
        add_task(ns)
    elif action == "approval-add":
        for k in ("platform", "summary", "file", "action", "spend", "site_dir", "site_name"):
            setattr(ns, k, getattr(ns, k, None))
        if getattr(ns, "agent", None) not in AGENTS or getattr(ns, "kind", None) not in KINDS or not getattr(ns, "title", ""):
            raise SystemExit("team: approval-add needs agent, kind and title")
        approval_add(ns)
    else:
        raise SystemExit("team: action must be add-task or approval-add")
    path.unlink()  # processed


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("submit")
    r.add_argument("file")
    t = sub.add_parser("add-task")
    t.add_argument("--by", choices=AGENTS, required=True)
    t.add_argument("--type", choices=server.TYPES, required=True)
    t.add_argument("--priority", choices=server.PRIORITIES, default="normal")
    t.add_argument("--title", required=True)
    t.add_argument("--details")
    p = sub.add_parser("approval-add")
    p.add_argument("--agent", choices=AGENTS, required=True)
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--title", required=True)
    for opt in ("--platform", "--summary", "--file", "--action", "--spend", "--site-dir", "--site-name"):
        p.add_argument(opt)
    l = sub.add_parser("approval-list")
    l.add_argument("--status")
    l.add_argument("--kind")
    s = sub.add_parser("approval-set")
    s.add_argument("id")
    s.add_argument("--status", required=True)
    s.add_argument("--result")
    a = ap.parse_args()
    {"submit": submit, "add-task": add_task, "approval-add": approval_add, "approval-list": approval_list,
     "approval-set": approval_set}[a.cmd](a)


if __name__ == "__main__":
    main()

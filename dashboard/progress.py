"""Progress reporter for workflow runs; the dashboard reads tasks/status.json.

  python dashboard/progress.py start --run "Queue worker"
  python dashboard/progress.py step  --task "<exact task title>" --type research --step 2 [--note "12 sources so far"]
  python dashboard/progress.py done  --task "<exact task title>" [--blocked]
  python dashboard/progress.py end
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "tasks" / "status.json"
LOCK = STATUS.with_suffix(".lock")

STEPS = {
    "research": ["Planning", "Searching", "Reading sources", "Verifying", "Writing report"],
    "code": ["Setting up branch", "Reading code", "Implementing", "Testing", "Handing off"],
    "docs": ["Reading inputs", "Transforming", "Checking output", "Writing notes"],
    "brief": ["Gathering news", "Writing brief"],
    "digest": ["Reading today's work", "Writing digest"],
    "web": ["Planning the site", "Building", "Checking quality", "Handing off"],
    "marketing": ["Researching audience", "Drafting", "Reviewing", "Submitting for approval"],
    "plan": ["Reviewing work", "Planning", "Assigning tasks"],
    "other": ["Working"],
}


def now():
    return datetime.now().isoformat(timespec="seconds")


class Locked:
    def __enter__(self):
        for _ in range(100):
            try:
                os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL))
                return self
            except FileExistsError:
                if time.time() - LOCK.stat().st_mtime > 10:  # stale lock from a crashed writer
                    LOCK.unlink(missing_ok=True)
                time.sleep(0.1)
        raise SystemExit("progress: could not lock status file")

    def __exit__(self, *a):
        LOCK.unlink(missing_ok=True)


def load():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"run": None, "tasks": {}, "recent": []}


def save(s):
    s["heartbeat"] = now()
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, STATUS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "step", "done", "end"])
    ap.add_argument("--run", default="Manual run")
    ap.add_argument("--task")
    ap.add_argument("--type", default="other")
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--note", default="")
    ap.add_argument("--blocked", action="store_true")
    a = ap.parse_args()

    with Locked():
        s = load()
        cutoff = (datetime.now() - timedelta(hours=2)).isoformat()
        s["tasks"] = {k: v for k, v in s.get("tasks", {}).items() if v.get("updated", "") > cutoff}
        run = s.get("run")

        if a.cmd == "start":
            s["run"] = {"name": a.run, "started": now(), "ended": None, "done": 0, "blocked": 0}
        elif a.cmd == "end":
            if run:
                run["ended"] = now()
        else:
            if not a.task:
                raise SystemExit("progress: --task is required")
            if not run or run.get("ended"):  # a step outside start/end still shows up
                s["run"] = run = {"name": a.run, "started": now(), "ended": None, "done": 0, "blocked": 0}
            title = " ".join(a.task.split())
            if a.cmd == "step":
                t = s["tasks"].get(title, {"started": now()})
                typ = a.type if a.type in STEPS else t.get("type", "other")
                steps = STEPS[typ]
                n = max(1, min(a.step, len(steps)))
                t.update(type=typ, step=n, total=len(steps), label=steps[n - 1], note=a.note, updated=now())
                s["tasks"][title] = t
            else:
                t = s["tasks"].pop(title, {})
                run["blocked" if a.blocked else "done"] += 1
                s["recent"] = ([{"title": title, "type": t.get("type", a.type), "finished": now(), "blocked": a.blocked}]
                               + s.get("recent", []))[:5]
        save(s)


if __name__ == "__main__":
    main()

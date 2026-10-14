"""Start the team when work is waiting.

  python dashboard/dispatch.py         -> prints START or IDLE, plus a reason (check only)
  python dashboard/dispatch.py start   -> if START, launches a headless queue run with the Claude Code CLI and
                                          prints STARTED; prints NO_CLI if the CLI isn't installed or signed in,
                                          so the caller can fall back to the scheduled task

A run counts as active while tasks/status.json shows it unfinished with a heartbeat in the last 20 minutes.
Headless (-p) runs can't freeze on a permission prompt: a command that isn't pre-approved is refused instead.
The dashboard calls start() after you add a task; team.py calls it after the Manager assigns work.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

ROOT = server.ROOT
PROMPT = Path(__file__).resolve().parent / "prompts" / "queue.md"
LOCK = ROOT / "tasks" / "dispatch.lock"
RUN_LOGS = ROOT / "logs" / "cli-runs"


def check():
    waiting = [t for t in server.parse_inbox() if t["state"] == "queued"]
    prog = server.read_status() or {}
    run = prog.get("run") or {}
    if run and not run.get("ended") and prog.get("heartbeat"):
        quiet = (datetime.now() - datetime.fromisoformat(prog["heartbeat"])).total_seconds() / 60
        if quiet < 20 and waiting:
            return "IDLE", f"{len(waiting)} waiting, but a run is already working ({run.get('name')})"
    if not waiting:
        return "IDLE", "nothing waiting in the queue"
    # A run that was just launched hasn't reported progress yet: don't launch a second one.
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300:
        return "IDLE", f"{len(waiting)} waiting; a run was started in the last 5 minutes"
    return "START", f"{len(waiting)} task(s) waiting"


def cli_path():
    for c in (shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe")):
        if c and Path(c).exists():
            return c
    return None


def cli_ready(exe):
    try:
        out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30, cwd=ROOT)
        return json.loads(out.stdout or "{}").get("loggedIn") is True
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def start():
    state, why = check()
    if state != "START":
        return state, why
    exe = cli_path()
    if not exe or not cli_ready(exe):
        return "NO_CLI", "Claude Code CLI not installed or not signed in"
    LOCK.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    RUN_LOGS.mkdir(parents=True, exist_ok=True)
    log = open(RUN_LOGS / f"{datetime.now():%Y-%m-%d_%H%M%S}.log", "w", encoding="utf-8")
    flags = 0
    if os.name == "nt":  # survive the dashboard closing; no console window
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([exe, "-p", PROMPT.read_text(encoding="utf-8")], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    return "STARTED", why


if __name__ == "__main__":
    state, why = start() if sys.argv[1:] == ["start"] else check()
    print(f"{state}: {why}")

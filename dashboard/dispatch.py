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
    # A run that was just launched hasn't reported progress yet: don't launch a second one,
    # unless that launch has already died (for example on a sign-in hiccup).
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300 and _alive(_lock_pid()):
        return "IDLE", f"{len(waiting)} waiting; a run was started in the last 5 minutes"
    return "START", f"{len(waiting)} task(s) waiting"


def _lock_pid():
    try:
        return int(LOCK.read_text(encoding="utf-8").split()[1])
    except (OSError, IndexError, ValueError):
        return None


def _alive(pid):
    """Is the launched CLI process still running? (No pid recorded: assume it is.)"""
    if not pid:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes  # os.kill(pid, 0) would terminate the process on Windows, so ask the OS instead
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259           # STILL_ACTIVE


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
    RUN_LOGS.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":  # survive the dashboard closing; no console window
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    for attempt in range(3):
        path = RUN_LOGS / f"{datetime.now():%Y-%m-%d_%H%M%S}.log"
        with open(path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen([exe, "-p", PROMPT.read_text(encoding="utf-8")], cwd=ROOT, stdout=log,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        LOCK.write_text(f"{datetime.now().isoformat(timespec='seconds')} {proc.pid}", encoding="utf-8")
        # A launch that dies within seconds hit a hiccup (e.g. two processes refreshing the sign-in at once):
        # wait a moment and try again instead of leaving the queue stuck until the next check.
        for _ in range(20):
            time.sleep(2)
            if proc.poll() is not None:
                break
        if proc.poll() is None or proc.returncode == 0:
            return "STARTED", why
        time.sleep(30)
    LOCK.unlink(missing_ok=True)
    return "FAILED", f"the run exited at once 3 times; see {path.relative_to(ROOT)}"


if __name__ == "__main__":
    state, why = start() if sys.argv[1:] == ["start"] else check()
    print(f"{state}: {why}")

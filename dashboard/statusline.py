"""Claude Code status line that also records your plan limits for the Foreman AI dashboard.

Claude Code runs this after each response and pipes it a JSON snapshot that includes
rate_limits.five_hour / rate_limits.seven_day (used_percentage, resets_at) on Pro/Max plans.
We save that to tasks/claude_limits.json, then print a short status line. If you already had a
status line, it still runs and its output is what you see (tasks/usage.json → statusline_chain).

Installed from the dashboard (AI usage → Connect Claude plan), which sets ~/.claude/settings.json:
  "statusLine": {"type": "command", "command": "\"...python.exe\" \"...\\dashboard\\statusline.py\""}
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tasks" / "claude_limits.json"
CFG = ROOT / "tasks" / "usage.json"


def main():
    raw = sys.stdin.buffer.read()
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        d = {}
    rl = d.get("rate_limits")
    if isinstance(rl, dict) and rl:
        try:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            tmp = OUT.with_suffix(".tmp")
            tmp.write_text(json.dumps({"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                       "rate_limits": rl}), encoding="utf-8")
            os.replace(tmp, OUT)
        except OSError:
            pass

    chain = ""
    try:
        chain = json.loads(CFG.read_text(encoding="utf-8")).get("statusline_chain", "")
    except (OSError, ValueError):
        pass
    if chain:
        try:
            r = subprocess.run(chain, input=raw, capture_output=True, shell=True, timeout=5)
            sys.stdout.buffer.write(r.stdout)
            return
        except (OSError, subprocess.SubprocessError):
            pass

    model = (d.get("model") or {}).get("display_name") or ""
    parts = [model] if model else []
    for key, label in (("five_hour", "5h"), ("seven_day", "week")):
        w = (rl or {}).get(key) or {}
        if w.get("used_percentage") is not None:
            parts.append(f"{label} {float(w['used_percentage']):.0f}%")
    print(" · ".join(parts))


if __name__ == "__main__":
    main()

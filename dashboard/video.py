"""Video Studio inside Foreman AI.

The studio page (video-studio.html) renders videos in the browser. This module gives it two things:
  * /api/video/claude: the studio's "ask Claude" calls (edit plans, medal scans, captions, styles),
    run through the local Claude Code CLI with the frame images saved as files it can Read.
  * /api/video/save: finished MP4s saved into outputs/videos/.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import dispatch

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "outputs" / "videos"
WORK = VIDEOS / ".work"
MODEL = os.environ.get("AIWF_VIDEO_MODEL", "sonnet")
QUICK_MODEL = os.environ.get("AIWF_VIDEO_QUICK_MODEL", "haiku")
MAX_IMAGES = 8
MAX_VIDEO_BYTES = 400 * 1024 * 1024


class VideoError(Exception):
    def __init__(self, message, code="local_error"):
        super().__init__(message)
        self.code = code


def status():
    return {"ready": bool(dispatch.cli_path())}


def extract_json(text):
    t = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                pass
    raise VideoError("Claude's answer wasn't valid JSON", "invalid_json")


def ask_claude(body):
    exe = dispatch.cli_path()
    if not exe:
        raise VideoError("The Claude Code CLI isn't installed on this PC", "sampling_disabled")
    prompt = str(body.get("prompt") or "")[:200_000]
    if not prompt.strip():
        raise VideoError("Empty request", "invalid_request")
    images = list(body.get("images") or [])[:MAX_IMAGES]
    want_json = body.get("json") is True
    model = QUICK_MODEL if body.get("tier") == "quick" else MODEL

    job = WORK / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    try:
        paths = []
        for i, b64 in enumerate(images):
            data = base64.b64decode(str(b64), validate=False)
            if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
                raise VideoError("Only JPEG or PNG images can be sent", "image_rejected")
            p = job / f"image-{i + 1}.{'png' if data[:4] == b'\x89PNG' else 'jpg'}"
            p.write_bytes(data)
            paths.append(str(p))
        if paths:
            prompt += ("\n\nThe attached images are these files, in order. Open every one with the Read tool "
                       "before answering:\n" + "\n".join(paths))
        if want_json:
            prompt += "\n\nReply with only the JSON. No other text."
        cmd = [exe, "-p", prompt, "--output-format", "json", "--model", model,
               "--allowedTools", "Read",
               "--disallowedTools", "Agent,Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                 timeout=420, stdin=subprocess.DEVNULL, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise VideoError("Claude took too long. Try fewer clips at once.", "timeout")
        try:
            data = json.loads(out.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise VideoError("Claude didn't answer. Check that the CLI is signed in (claude auth login).",
                             "sampling_disabled")
        if data.get("is_error"):
            raise VideoError(str(data.get("result") or "Claude couldn't answer that.")[:300], "rate_limited"
                             if "limit" in str(data.get("result", "")).lower() else "local_error")
        text = str(data.get("result") or "").strip()
        if not text:
            raise VideoError("Claude's answer was empty", "empty_completion")
        return {"value": extract_json(text)} if want_json else {"text": text}
    finally:
        shutil.rmtree(job, ignore_errors=True)


def save(name, data):
    if not data:
        raise VideoError("Nothing to save", "invalid_request")
    stem = re.sub(r"[^\w\- ]+", "", Path(str(name or "video")).stem).strip().replace(" ", "-")[:60] or "video"
    ext = ".webm" if str(name).lower().endswith(".webm") else ".mp4"
    VIDEOS.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now():%Y-%m-%d}-{stem}"
    p = VIDEOS / f"{base}{ext}"
    n = 2
    while p.exists():
        p = VIDEOS / f"{base}-{n}{ext}"
        n += 1
    p.write_bytes(data)
    return {"where": str(p.relative_to(ROOT)).replace("\\", "/"), "path": str(p)}


def open_folder():
    VIDEOS.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(VIDEOS))  # Windows Explorer
    return {"ok": True}

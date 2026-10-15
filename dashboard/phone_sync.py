"""Prepares data for the phone page (a claude.ai artifact) and imports tasks added from the phone.

The artifact's database is only reachable through Claude's ArtifactData tool, so the "ai-phone-sync"
scheduled task runs these commands and moves the files to and from the database:

  python dashboard/phone_sync.py build            -> tasks/phone/state.json + tasks/phone/new_reports/*.json
  python dashboard/phone_sync.py import FILE.json -> appends phone tasks (a JSON list of inbox docs) to tasks/inbox.md
  python dashboard/phone_sync.py uploaded ID...   -> records report ids that are now in the database
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402  (reuses the dashboard's parsers; importing does not start the server)

ROOT = server.ROOT
PHONE = ROOT / "tasks" / "phone"
STATE = PHONE / "state.json"
NEW_REPORTS = PHONE / "new_reports"
UPLOADED = PHONE / "uploaded_reports.json"
IMPORTED = PHONE / "imported_tasks.json"
REPORT_MAX = 180_000  # stay well under the database's 256 KiB document limit


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def report_id(rel_path):
    p = Path(rel_path)  # outputs/<kind>/<name>.md -> "<kind>-<name>", so a brief and a digest of one day differ
    return re.sub(r"[^A-Za-z0-9_\-.]", "-", f"{p.parent.name}-{p.stem}")[:180]


def next_run(s, now):
    for add in range(8):
        day = now + timedelta(days=add)
        if day.weekday() not in s["days"]:
            continue
        for t in s["times"]:
            h, m = map(int, t.split(":"))
            c = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if c > now:
                return c.isoformat(timespec="minutes")
    return None


def build():
    now = datetime.now()
    st = server.state()
    prog = st["progress"] or {}
    run = prog.get("run") or {}
    quiet = None
    if prog.get("heartbeat"):
        quiet = (now - datetime.fromisoformat(prog["heartbeat"])).total_seconds() / 60
    run_state = "idle" if (not run or run.get("ended")) else ("stale" if quiet is not None and quiet >= 15 else "working")

    reports = [o for o in st["outputs"] if o["ext"] == ".md" and not o["name"].endswith(".notes.md")][:15]
    report_ids = {o["path"]: report_id(o["path"]) for o in reports}

    def report_for(output):
        path = (output or "").split()[0] if output else ""
        return report_ids.get(path)

    state = {
        "updated": now.isoformat(timespec="seconds"),
        "run": {"state": run_state, "name": run.get("name"), "started": run.get("started"),
                "ended": run.get("ended"), "heartbeat": prog.get("heartbeat")},
        "running": [{"title": k, "type": v.get("type"), "step": v.get("step"), "total": v.get("total"),
                     "label": v.get("label"), "note": v.get("note")}
                    for k, v in (prog.get("tasks") or {}).items()] if run_state != "idle" else [],
        "stats": st["stats"],
        "queue": [{"title": t["title"], "type": t["type"], "priority": t["priority"], "state": t["state"],
                   "details": t["details"][:400]} for t in st["tasks"]],
        "done": [{"title": d["title"], "type": d["type"], "date": d["date"], "time": d["time"],
                  "output": d["output"], "report": report_for(d["output"])}
                 for d in st["done"] if d["status"] == "done"][:25],
        "blocked": [{"title": d["title"], "type": d["type"], "date": d["date"], "reason": d["reason"]}
                    for d in st["done"] if d["status"] == "blocked"][:25],
        "schedules": [{"name": s["name"], "next": next_run(s, now), "event": bool(s.get("event"))}
                      for s in server.SCHEDULES],
        "approvals": [{"id": a["id"], "title": a["title"], "agent": a["agent"], "kind": a["kind"], "platform": a.get("platform", ""),
                       "spend": a.get("spend", ""), "summary": a.get("summary", "")[:400], "created": a["created"],
                       "status": a["status"], "decided": a.get("decided"), "note": a.get("note", ""), "result": a.get("result", ""),
                       "report": report_ids.get((a.get("file") or "").strip())}
                      for a in st.get("approvals", [])][:25],
        # what the phone needs to mirror the PC overview: hand-off trails, timeline, demo sites
        "history": [{k: h.get(k) for k in ("ts", "title", "type", "from", "project")} for h in st.get("history", [])][-150:],
        "sites": [{"title": s["title"].split("|")[0].strip(), "slug": s["slug"], "modified": s["modified"],
                   "report": report_ids.get(s.get("summary") or "")} for s in st.get("sites", [])],
        "blocked_all": [{"title": d["title"], "type": d["type"], "date": d["date"], "time": d["time"], "reason": d["reason"]}
                        for d in st["done"] if d["status"] == "blocked"][:25],
        "reports": [{"id": report_ids[o["path"]], "title": o["title"], "kind": o["kind"], "modified": o["modified"]}
                    for o in reports],
    }
    # Jarvis-as-hub on the phone: weather and the latest news brief travel with each sync
    try:
        import hub
        wx = hub.weather()
        state["weather"] = {k: wx[k] for k in ("place", "now", "today", "tomorrow", "summary", "fetched")}
    except Exception:
        state["weather"] = None
    briefs = sorted((ROOT / "outputs" / "briefs").glob("*.md"), reverse=True)
    if briefs:
        text = re.sub(r"\[(\d+|[^\]]{0,80})\]\([^)]*\)", r"\1", server.read(briefs[0]))   # drop link targets
        state["news_brief"] = {"date": briefs[0].stem[:10], "text": text[:3500]}
    PHONE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")

    # Reports the phone doesn't have yet, one JSON file per database document.
    uploaded = set(load_json(UPLOADED, []))
    NEW_REPORTS.mkdir(parents=True, exist_ok=True)
    for old in NEW_REPORTS.glob("*.json"):
        old.unlink()
    pending = []
    for o in reports:
        rid = report_ids[o["path"]]
        if rid in uploaded:
            continue
        md = server.read(ROOT / o["path"])
        if len(md) > REPORT_MAX:
            md = md[:REPORT_MAX] + "\n\n*(Report shortened for the phone. The full version is on your PC.)*"
        doc = {"title": o["title"], "kind": o["kind"], "path": o["path"], "modified": o["modified"], "markdown": md}
        (NEW_REPORTS / f"{rid}.json").write_text(json.dumps(doc), encoding="utf-8")
        pending.append(rid)
    print(json.dumps({"state": str(STATE), "new_reports": pending, "new_reports_dir": str(NEW_REPORTS)}))


def import_tasks(path):
    docs = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(docs, dict):  # accept {"documents": [...]} or a single doc
        docs = docs.get("documents") or docs.get("docs") or [docs]
    imported = set(load_json(IMPORTED, []))
    added = []
    for d in docs:
        body = d.get("data", d)
        doc_id = str(d.get("id") or d.get("doc_id") or body.get("id") or "")
        if not doc_id or doc_id in imported or body.get("status") != "new":
            continue
        if body.get("kind") == "decision":   # approve / reject made on the phone's Approvals tab
            try:
                server.decide_approval({"id": body.get("approval_id", ""), "decision": body.get("decision", ""),
                                        "note": body.get("note", "")})
            except ValueError as e:
                print(json.dumps({"skipped_decision": doc_id, "why": str(e)}))
            imported.add(doc_id)
            added.append(doc_id)
            continue
        if body.get("kind") == "feedback":   # feedback sent from the phone (Jarvis), same as the dashboard's button
            try:
                server.give_feedback({"title": body.get("title", ""), "feedback": body.get("feedback") or body.get("details", "")})
            except ValueError as e:
                print(json.dumps({"skipped_feedback": doc_id, "why": str(e)}))
            imported.add(doc_id)
            added.append(doc_id)
            continue
        typ = body.get("type") if body.get("type") in server.TYPES else "auto"
        prio = body.get("priority") if body.get("priority") in server.PRIORITIES else "normal"
        details = str(body.get("details") or "")
        server.add_task({"type": typ, "priority": prio, "title": body.get("title", ""),
                         "summary": "added from phone", "details": details})
        imported.add(doc_id)
        added.append(doc_id)
    IMPORTED.write_text(json.dumps(sorted(imported)), encoding="utf-8")
    print(json.dumps({"imported": added}))


def mark_uploaded(ids):
    done = set(load_json(UPLOADED, [])) | set(ids)
    UPLOADED.write_text(json.dumps(sorted(done)), encoding="utf-8")
    print(json.dumps({"uploaded": len(done)}))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build()
    elif cmd == "import" and len(sys.argv) == 3:
        import_tasks(sys.argv[2])
    elif cmd == "uploaded":
        mark_uploaded(sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(2)

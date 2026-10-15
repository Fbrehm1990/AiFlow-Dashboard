---
name: run-queue
description: Process the task inbox unattended. Routes "Team decides" tasks through the Manager, then hands up to 3 tasks by priority to the right team agent (researcher, web-builder, marketer). Use when asked to "run the queue", "work the inbox", or by the scheduled worker.
---

# Run the queue

1. Read `CLAUDE.md` (the hard rules apply) and `tasks/inbox.md`. Ignore the example comment block.
   Run `python dashboard/progress.py start --run "Queue worker"` immediately, and `... end` as the very last step.
   Every subagent prompt must include the "Progress reporting" and "Narrate as you work" sections of
   CLAUDE.md, with the exact task title filled in, so the dashboard shows each task's step and reasoning live.
2. Collect the open `- [ ]` tasks under `## Queue`. Skip `- [~]` (in progress elsewhere).
   If a `[~]` task's log entry is more than 3 hours old, treat it as stale and reset it to `[ ]`.
3. Order them: owner feedback first (`Revise: …` tasks marked "your feedback"), then by priority (high,
   normal, low), then top to bottom. Hand each revision to the agent who did the original (by its type),
   with the "Owner feedback" section of CLAUDE.md.
4. **Route "Team decides" tasks first** (`[auto]`, or no tag). For each one, mark it `[~]` and hand it to the
   `manager` agent with the instruction "Triage this owner task", as described in `.claude/agents/manager.md`.
   The Manager answers with one of two things:
   - **One agent** (route `research`, `web`, `marketing`, `code`, `brief` or `docs`). Rewrite the inbox line's tag
     from `[auto]` to that type, set it back to `[ ]`, and treat it as a normal task in this same run if you
     have room.
   - **Several steps.** The Manager has already submitted them as new tasks (marked "step of your task").
     Move the original to `tasks/done.md` as `- [x] … [auto] <title> → routed into N steps: <step titles>`.
   Triage counts toward this run's 6 tasks only when it turned into a single task that ran.
5. Work in batches of up to 3 tasks at a time. When a batch finishes, re-read the inbox and take the next
   batch, including steps the Manager split out of an owner task. Stop when the queue is empty or you've
   finished **6 tasks** this run; anything left starts a new run at the next dispatch check. For each task:
   - Mark it `[~]` in the inbox and save the file.
   - Hand it to the right team member: Agent tool with `subagent_type` from the team table in `CLAUDE.md`
     (`researcher`, `web-builder`, `marketer`, `manager`), giving the full task text, the skill to follow,
     and the instruction to follow `CLAUDE.md`. Independent tasks may run in parallel, but two
     `[web]`/`[code]` tasks on the same site or repo must not. If a named agent type isn't available, use
     `general-purpose` with the agent's file (`.claude/agents/<name>.md`) pasted in as its instructions.
   - When it returns, check the deliverable exists and is non-trivial. If it is weak, do one improvement pass.
   - Do the bookkeeping from `CLAUDE.md` (move the task to done.md and append to the log).
6. If the inbox is empty, append "queue empty" to the log and stop. Don't invent work.
7. Finish with a 3–5 line summary of what was done, which agent did each task, and where the outputs are.

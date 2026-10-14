---
name: run-queue
description: Process the task inbox unattended. Picks open tasks from tasks/inbox.md by priority and dispatches each to the matching skill (deep-research, coding-task, daily-brief, docs-task). Use when asked to "run the queue", "work the inbox", or by the scheduled worker.
---

# Run the queue

1. Read `CLAUDE.md` (the hard rules apply) and `tasks/inbox.md`. Ignore the example comment block.
   Run `python dashboard/progress.py start --run "Queue worker"` immediately, and `... end` as the very last step.
   Every subagent prompt must include the "Progress reporting" and "Narrate as you work" sections of
   CLAUDE.md, with the exact task title filled in, so the dashboard shows each task's step and reasoning live.
2. Collect the open `- [ ]` tasks under `## Queue`. Skip `- [~]` (in progress elsewhere).
   If a `[~]` task's log entry is more than 3 hours old, treat it as stale and reset it to `[ ]`.
3. Order them by priority: high, then normal, then low. Within a priority, take them top to bottom.
4. Process up to **3 tasks** this run (raise the limit only if they are small). For each task:
   - Mark it `[~]` in the inbox and save the file.
   - Hand it to the right team member: Agent tool with `subagent_type` from the team table in `CLAUDE.md`
     (`researcher`, `web-builder`, `marketer`, `manager`), giving the full task text, the skill to follow,
     and the instruction to follow `CLAUDE.md`. Independent tasks may run in parallel, but two
     `[web]`/`[code]` tasks on the same site or repo must not. If a named agent type isn't available, use
     `general-purpose` with the agent's file (`.claude/agents/<name>.md`) pasted in as its instructions.
   - When it returns, check the deliverable exists and is non-trivial. If it is weak, do one improvement pass.
   - Do the bookkeeping from `CLAUDE.md` (move the task to done.md and append to the log).
5. If the inbox is empty, append "queue empty" to the log and stop. Don't invent work.
6. Finish with a 3–5 line summary of what was done and where the outputs are.

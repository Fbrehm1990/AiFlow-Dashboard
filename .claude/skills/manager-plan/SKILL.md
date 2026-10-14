---
name: manager-plan
description: The Manager's morning planning run. Review the team's work, judge quality, decide next moves toward tasks/goals.md, assign tasks with dashboard/team.py, and write outputs/plans/YYYY-MM-DD.md.
---

# Manager: morning plan

Run as the `manager` agent (see `.claude/agents/manager.md` for authority and limits).

1. `python dashboard/progress.py start --run "Manager plan"`, then
   `python dashboard/progress.py step --task "Daily plan" --type plan --step 1 --note "Reviewing the team's work"`
2. **Read:**
   - `tasks/goals.md`
   - `tasks/inbox.md`
   - the top 20 lines of `tasks/done.md`
   - the files in `outputs/` changed since your last plan (`outputs/plans/` has the previous one)
   - `tasks/approvals.json`
   - yesterday's and today's `logs/`
3. **Quality review.** For each new deliverable, write one line: ✅ ready for the owner, or ⚠ with the specific
   problem. Queue a fix only if the problem would embarrass the business or block the next step.
4. `progress.py step ... --step 2 --note "Choosing next moves"`. **Decide.** Choose up to 4 assignments for
   today that move the goals forward most, using what the team already has. Keep 1–3 tasks per agent in
   flight, and don't queue work the owner removed or rejected. Respect what's pending approval: don't make
   more marketing for a campaign the owner hasn't approved yet.
5. `progress.py step ... --step 3 --note "Assigning tasks"`. **Assign** each one with
   a request file (`tasks/requests/plan-<n>.json`, written with the Write tool) and
   `python dashboard/team.py submit tasks/requests/plan-<n>.json`, as in your charter. Never put task text
   on the command line. Make tasks specific and self-contained:
   - inputs: exact file paths
   - deliverable: what and where
   - acceptance criteria
   - which agent it's for, given through the type: `research` / `brief` / `docs` go to the Researcher,
     `web` / `code` to the Web builder, `marketing` to the Marketer
6. **Write `outputs/plans/YYYY-MM-DD.md`:**
   ```
   # Team plan: <date>
   ## Scoreboard (progress against each goal: numbers where possible)
   ## Quality review (✅/⚠ per deliverable)
   ## Today's assignments (agent · task · why it matters)
   ## Waiting on you (pending approvals, decisions, blockers, in priority order)
   ## Risks & suggestions (max 3)
   ```
   Keep it under about 60 lines. The owner reads it on a phone.
7. Bookkeeping: append a log entry, then `progress.py done --task "Daily plan"` and `progress.py end`.

If `tasks/goals.md` still contains `[fill in]` placeholders that block good decisions, put the questions
at the top of "Waiting on you" and plan around what is known. Don't stall.

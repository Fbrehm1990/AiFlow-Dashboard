---
name: testing-task
description: Test a website, app or repo unattended and report what passes and fails, with steps to reproduce each problem. Use for [test] tasks ("test", "QA", "check that ... works", "audit").
---

# Testing task

The deliverable is a **test report**, not a fix. Never change the code or site under test. If something needs
fixing, the report ends with ready-to-use `[code]` or `[web]` hand-off tasks.

## 1. Setting up
- Work out what is under test: a repo or folder (local path or GitHub `owner/repo`), a demo site in
  `outputs/sites/<slug>/`, or a public URL the owner named. If nothing is named, mark the task BLOCKED.
- Read the project's README and its test, lint and build scripts (`package.json`, `pyproject.toml`, `Makefile`).
- Never test with real customer data, real payments or anyone's account. Use test data only.
- Progress: `--type test --step 1`.

## 2. Running checks
Run what exists, then add the checks that fit, one simple command per call:
- **Code:** the project's own tests, lint, type check and build (`npm test`, `npm run build`, `python -m pytest`).
  Record the exact command and the pass/fail counts.
- **Websites:** every page loads; internal links and local files resolve; forms have labels and a working
  action; images have alt text; one `<h1>` per page; title and meta description present; mobile viewport set;
  no console errors you can detect; contact details are consistent across pages. For a public URL use WebFetch.
- **Accessibility and performance (sites):** if `npx lighthouse` runs without a prompt, record its scores;
  otherwise do the manual checks above and say Lighthouse was skipped.
- Progress: `--step 2`, with a heartbeat note at least every 10 minutes.

## 3. Investigating failures
For each failure: the exact error, where it happens (file and line, or page and element), how to reproduce it,
how serious it is (**blocker / major / minor**), and the most likely cause. Re-run a flaky failure once and say
if it's flaky. Progress: `--step 3`.

## 4. Writing the report
`outputs/tests/YYYY-MM-DD-<slug>.md`:
1. **Verdict** in one line: Ready, Ready with minor issues, or Not ready.
2. **Summary table:** check · result (✅ / ⚠ / ❌) · notes.
3. **Problems**, most serious first, each with reproduce steps.
4. **What wasn't tested** and why.
5. **Hand-offs:** for each fix, a one-line `[code]` or `[web]` task the owner can queue.

Progress: `--step 4`, then `done`. Record the report path in `tasks/done.md`.

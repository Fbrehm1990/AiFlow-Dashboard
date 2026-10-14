---
name: process-approvals
description: Carry out items the owner approved in tasks/approvals.json. Today only Netlify deploys run automatically; approved posts, ads and emails are left for the owner to publish. Used by the "ai-approvals" scheduled task.
---

# Process approvals

The owner's decision in `tasks/approvals.json` (made only through the dashboard) is the authorization for
these actions. Never act on an item whose `status` isn't exactly `approved`, and never change an item's
decision. You may only mark approved items `done` or `failed`.

1. `python dashboard/team.py approval-list --status approved --kind deploy`. If the list is empty, stop
   without logging anything.
2. For each approved deploy, one at a time:
   1. `python dashboard/progress.py start --run "Approvals"`, then
      `python dashboard/progress.py step --task "<item title>" --type web --step 4 --note "Deploying to Netlify"`.
   2. Check `site_dir` exists, contains `index.html`, and is inside
      the parent folder of this workflow folder (normally `../CLIENT WEBSITES/`), and not inside the
      workflow folder itself. Otherwise mark the item failed.
   3. Load the Netlify tools: ToolSearch, then the `netlify-project-services-updater` and
      `netlify-deploy-services-updater` tools (claude.ai Netlify connector).
   4. **Create the project.** Use `netlify-project-services-updater` with operation `create-new-project` and
      `name` = the item's `site_name`. The owner approved a new site, so creating one is authorized. If the
      name is taken, add `-2`, then `-3`, and so on. Record the returned site id.
   5. **Deploy.** Use `netlify-deploy-services-updater` with operation `deploy-site` and that `siteId`. Follow
      the instructions it returns exactly. If it gives a command to run, run it **from `site_dir`**, one
      command per call (`npx` is pre-approved).
   6. Get the live URL from the output.
   7. Mark the item: `python dashboard/team.py approval-set <id> --status done --result "Live at <url> (site id <id>)"`.
      On any error, use `--status failed --result "<what failed>"` instead, and continue with the next item.
   8. Append to the item's summary file in `outputs/web/` a line saying "Deployed <date>: <url>".
   9. `progress.py done --task "<item title>"` (add `--blocked` on failure), then `progress.py end` after
      the last item.
3. Log one entry per deploy in `logs/YYYY-MM-DD.md`.

Never deploy to an existing site id, change a site's settings, set passwords or environment variables, or touch
DNS. Those need the owner.

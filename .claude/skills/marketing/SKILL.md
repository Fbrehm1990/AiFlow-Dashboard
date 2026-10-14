---
name: marketing
description: Produce ready-to-publish marketing for the web design business — Facebook/Instagram posts and calendars, TikTok scripts, Google Ads campaigns, email outreach — and submit each public or paid item for owner approval. Use for [marketing] tasks.
---

# Marketing

Every deliverable goes in `outputs/marketing/YYYY-MM-DD-<slug>.md` and opens with:
- **Goal:** the result it's for, such as discovery calls, demo-site views or replies
- **Audience:** who it targets
- **Platform(s)**
- **What the owner needs to do:** the exact publishing steps after approval

Then the content itself, ready to copy and paste.

## Platform formats
**Facebook / Instagram**
- **Post:** a hook in the first line, under 125 characters before the "more" cut; a body of up to about
  150 words; one clear call to action; 3–8 relevant local and industry hashtags (Instagram only).
- **Image brief:** what the visual shows, the text on it (7 words or fewer), and the 1080×1350 or 1080×1080
  format.
- **Calendars:** a table of date/time, format (post / carousel / Reel / Story), hook, caption, visual brief and
  CTA. 3–5 posts a week maximum.
- **Before/after or demo showcases** of the Web builder's sites are the strongest content. Use them.

**TikTok**
- **Script:** a 0–2s hook (on-screen text and spoken), a beat-by-beat script with timings for 15–45s, a shot
  list the owner can film on a phone, the on-screen captions, a caption of 150 characters or fewer, and 3–5
  hashtags.
- **Formats that work for web design:** "I built a website for a local <business> in 24h",
  before/after scroll-throughs, "3 things your website is costing you", and reactions to local sites.

**Google Ads (Search)**
- **Campaign:** goal, location targeting (radius or county), schedule, and a daily budget with reasoning.
- **Keywords:** 2–4 ad groups by intent, each with 10–20 keywords (phrase and exact), plus a negative keyword
  list (jobs, free, DIY, templates, courses…).
- **Responsive search ad per group:** 15 headlines of 30 characters or fewer and 4 descriptions of 90
  characters or fewer. **Count the characters** and show each count. Add path fields and suggested
  sitelinks and callouts.
- **Measurement:** the conversion to track (calls, form submits) and a kill or keep rule, for example "pause if
  CPC is over $X after 100 clicks".
- **Spend is a proposal only.** Put the amount and duration in `--spend`.

**Email outreach (drafts only)**
- One personalized email per lead, built from the Researcher's notes:
  - a subject line of 50 characters or fewer, honest, never clickbait
  - an opening line specific to their business
  - the problem
  - the demo-site link if there is one, or the offer
  - one simple ask
  - 120 words maximum
- A follow-up sequence: day 3 and day 8, shorter, each with something new.
- **CAN-SPAM:** a real sender name, a placeholder for the owner's postal address, a plain opt-out line, and
  no misleading header or subject.
- Never send. Never collect or guess personal email addresses. Use only addresses the business publishes.

## Rules for all content
- Honest and specific: no fake reviews, invented results or client names, false urgency or guarantees.
- Match the brand voice in `tasks/goals.md`. Local, friendly and confident beats corporate.
- Follow each platform's ad policies: no prohibited claims, no personal-attribute targeting language.

## Submit for approval
One approval per publishable unit, using a request file plus `python dashboard/team.py submit <file>` (see
`.claude/agents/marketer.md`):
- **kinds:** `post` (one post, or a calendar), `ad` (a campaign, with `--spend`), `email` (a sequence or
  batch)
- the `--summary` says the goal and audience in one line

Finish with **Hand-offs**: tasks for the Researcher (audience or competitor gaps) or the Web builder
(landing pages the ads or posts need).

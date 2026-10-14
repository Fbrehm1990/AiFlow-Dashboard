---
name: web-build
description: Build a website (static HTML/CSS/JS, mobile-first, accessible, SEO-ready). Demo sites for leads stay LOCAL in outputs/sites/<slug>/ for the owner to open; only sites for businesses that have agreed get a client repo and a Netlify deploy request. Use for [web] tasks.
---

# Web build

## Two kinds of site work. Decide which one the task is.

**1. Demo site (the default).** A pitch for a lead who hasn't agreed to anything.
- Build it in `outputs/sites/<slug>/`, inside this workflow folder. This is a **local preview only**.
- It must work when the owner just double-clicks `index.html` (opened as a `file://` page). That means:
  - every link and asset path is **relative** (`styles.css`, `assets/x.svg`, `menu.html`), never starting
    with `/`
  - no build step, no server-only features, no fetch of local JSON
  - Google Fonts links are fine
- **Never request a deploy for a demo.** Owner rule: nothing goes on Netlify until the business has agreed.
  Don't create a Netlify project, and don't put a "live URL" in the summary.
- No git repo is needed. Do keep a `netlify.toml` so the site is ready when the time comes.

**2. Client launch.** Do this only when the owner's task says the business **agreed**, for example
"Launch the site for X: they agreed on <date>".
- Copy the demo from `outputs/sites/<slug>/` (or build fresh) into `../CLIENT WEBSITES/<slug>/` (the
  "CLIENT WEBSITES" folder next to this workflow folder). `git init`, and commit on branch `ai/<slug>`.
- Replace the demo placeholders with the real details the owner supplied. Remove the "Demo site" footer
  note.
- Then request deploy approval, exactly as in `.claude/agents/web-builder.md`. The owner approves, and the
  approvals schedule deploys.
- If a task asks for a deploy and doesn't say the business agreed, don't deploy. Build or update the local
  demo, and note in the summary that a launch needs the owner to confirm the agreement.

## Build standards (the product)
- **Stack:** plain HTML, CSS and a little JS: `index.html` plus pages as needed, `styles.css`, `assets/`, and
  a `netlify.toml` with `[build] publish = "."` and sensible headers.
- **Content:** start from the Researcher's notes. Business name, category, address, phone and hours exactly
  as the business publishes them. Never invent reviews, awards, prices, staff or claims. Use clearly marked
  placeholders such as `[Add your best customer review]`. Demo sites show a small "Demo site prepared by
  <owner's business>" note in the footer.
- **Design:** distinctive and right for the trade. A bakery shouldn't look like an auto shop. Use a real
  type pairing (Google Fonts), a tight palette, a strong hero with a clear call to action (call, directions,
  book), services, about, hours and map link, contact. No generic stock-template look.
- **Images:** use CSS, SVG illustration or patterns, or royalty-free images the task gives you. Never hotlink
  or copy images from other sites.
- **Mobile-first:** works from 360px wide, with tap targets of at least 44px. Fast: no render-blocking JS,
  well under 1 MB total.
- **Accessibility:** semantic landmarks, alt text, labels, readable contrast, visible focus states.
- **SEO and local:** a title and meta description per page, Open Graph tags, LocalBusiness JSON-LD (only the
  facts you have), `robots.txt` and `sitemap.xml`. Add `noindex` for demos.
- **Forms:** Netlify Forms (`data-netlify="true"` plus a honeypot field) when a contact form is asked for. On
  a local demo the form won't submit, so say so in the summary.

## Check before handing off
- Every link and asset path resolves **relative to the file**, so nothing starts with `/`. Check with Glob,
  Read and Grep; don't guess.
- The HTML is well formed, the JSON-LD parses, and there are no lorem ipsum or TODOs except the marked
  placeholders.
- Read the page top to bottom as a phone user would: is the call to action obvious in the first screen?

## Hand off
1. Write `outputs/web/YYYY-MM-DD-<slug>.md`:
   - who it's for and what's on it
   - placeholders the owner must fill
   - **"To view it: open `outputs/sites/<slug>/index.html`, or click Open site on the dashboard"**
2. Demo: no approval request. Client launch: a deploy request (see above).
3. Add **Hand-offs** for the Marketer, such as screenshot ideas for outreach and the key selling points. A
   demo has no public link, so outreach offers to show the preview, and the owner can attach screenshots.

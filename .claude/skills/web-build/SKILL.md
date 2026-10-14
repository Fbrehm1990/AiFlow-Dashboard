---
name: web-build
description: Build a demo or client website (static HTML/CSS/JS, mobile-first, accessible, SEO-ready, Netlify-ready) in its own folder and git repo, check it, and request deploy approval. Use for [web] tasks.
---

# Web build

## Where
- New sites: `../CLIENT WEBSITES/<slug>/`, the "CLIENT WEBSITES" folder next to this workflow folder. Use its
  absolute path in commands and approvals. Create the folder, `git init`, and commit
  as you go on branch `ai/<slug>`. Existing sites: work in their repo on an `ai/<slug>` branch and never
  touch main or master.
- The summary for the owner goes in `outputs/web/YYYY-MM-DD-<slug>.md`.

## Build standards (the product)
- **Stack:** plain HTML, CSS and a little JS. No build step, no frameworks, unless the task asks for one.
  `index.html` plus pages as needed, `styles.css`, `assets/`, and a `netlify.toml` that has a `[build]`
  `publish = "."` section and sensible headers.
- **Content:** start from the Researcher's notes. Business name, category, address, phone and hours exactly
  as the business publishes them. Never invent reviews, awards, prices, staff or claims. Use clearly marked
  placeholders such as `[Add your best customer review]`. For demo sites, show a small "Demo site prepared by
  <owner's business>" note in the footer.
- **Design:** distinctive and right for the trade. A bakery shouldn't look like an auto shop. Use a real
  type pairing (Google Fonts), a tight palette, a strong hero with a clear call to action (call, directions,
  book), services, about, hours and map link, contact. No generic stock-template look.
- **Images:** use CSS, SVG illustration or patterns, or royalty-free images the task gives you. Never hotlink
  or copy images from other sites.
- **Mobile-first:** works from 360px wide, with tap targets of at least 44px. Fast: aim for no
  render-blocking JS and well under 1 MB total.
- **Accessibility:** semantic landmarks, alt text, labels, readable contrast, visible focus states.
- **SEO and local:** a title and meta description per page, Open Graph tags, LocalBusiness JSON-LD (only
  the facts you have), `robots.txt` and `sitemap.xml`.
- **Forms:** Netlify Forms (`data-netlify="true"`, a honeypot field) when a contact form is asked for.

## Check before handing off
- Every link and asset path resolves (use Glob and Read; don't guess).
- The HTML is well formed, the JSON-LD parses (`python -c "import json; json.load(open('<file>'))"` on an
  extracted copy if needed), and there are no lorem ipsum or TODOs except the marked placeholders.
- Read the page top to bottom as a phone user would: is the call to action obvious in the first screen?

## Hand off
1. Commit on `ai/<slug>`.
2. Write `outputs/web/YYYY-MM-DD-<slug>.md`: who it's for, what's on it, placeholders the owner must fill,
   the folder path, and how to preview it locally (`python -m http.server` inside the folder).
3. If the task says to deploy, or the site is a finished demo, request approval:
   write a deploy request file and submit it with `python dashboard/team.py submit tasks/requests/deploy-<slug>.json`,
   exactly as in `.claude/agents/web-builder.md` (`site_name` in lowercase-hyphenated form)
4. Add **Hand-offs** for the Marketer, for example "post showing the demo" or "outreach email linking the
   demo once it's deployed".

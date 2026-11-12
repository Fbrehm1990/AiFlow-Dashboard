---
name: deep-research
description: Multi-source web research producing a cited report with a clear recommendation. Use for [research] tasks or any "research / compare / investigate / find out" request.
---

**Answer tasks.** When the task title starts with `Answer:`, the owner asked Jarvis a question that needs real
research, and Jarvis reads your answer back to them. Start the report with a `## Short answer` section: 2–4
plain spoken sentences that directly answer the question (no markdown, links or citations inside it), then
the full cited report as usual. Keep the whole thing focused on the question.

# Deep research

## Method
1. **Frame**: restate the question, the decision it serves, and 4–8 sub-questions. Write down your assumptions.
2. **Search wide**: run several WebSearch queries per sub-question, worded differently, including
   recent-news, pricing, reviews/complaints and "X vs Y" angles. Prefer primary sources: official docs, pricing
   pages, filings, papers and changelogs. Read the actual pages with WebFetch. Don't rely on snippets alone.
3. **Go deep**: follow the most useful links one hop further. Aim for 10 or more distinct sources, and more for big topics.
   For broad topics, research the sub-questions in parallel with subagents and merge what they find.
4. **Verify**: cross-check every key number or claim against a second source. Flag anything stale (over 12
   months old), conflicting or single-sourced.
5. **Synthesize**: draw conclusions; don't just summarize. Rank the options and make a recommendation.

## Report: `outputs/research/YYYY-MM-DD-<slug>.md`
```
# <Title>
_Date · question · assumptions_

## Bottom line (3–5 sentences, the answer first)
## Key findings (bullets, each with [n] citations)
## Comparison table (if options are being compared)
## Details by sub-question
## Risks, unknowns & conflicting evidence
## Recommendation & next steps
## Sources ([n] Title, URL, accessed date)
```
Every factual claim gets a citation. Don't fabricate a source or a number. If you couldn't verify something, say so.

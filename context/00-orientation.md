# Context folder

Historical and explanatory docs. Read these to understand **why** the current code looks the way it does.

The top-level docs (`README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `ROADMAP.md`) describe **what** the code is and **how** to operate it. The files in this folder fill in the **why** — design pivots, failed approaches, ToS edge cases — so the next contributor doesn't repeat dead ends.

## Files

- **`01-decisions.md`** — architectural decisions log. FireCrawl → SerpApi pivot, why TDD vertical slices, sort-mode choices, cursor design rationale, schema evolution.
- **`02-firecrawl-pivot.md`** — full story of why FireCrawl can't reach Google Maps reviews. Save the next agent 90 minutes of wasted exploration.
- **`03-limits-quotas.md`** — external service caps: SerpApi free tier limits, Google Maps' ~5K display ceiling, throttle math.
- **`04-place-ids.md`** — the Google Maps Place IDs we use, how we sourced them, how to find new ones.
- **`05-tdd-plan.md`** — the original TDD plan written before implementation. Slices, RED → GREEN order, what was deferred.

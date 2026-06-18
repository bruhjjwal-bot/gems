# Decisions log

Reverse-chronological. Each entry: the decision, why we made it, what alternatives we rejected.

## 2026-06-18 — SerpApi for Google Reviews (replacing FireCrawl)

**Decision:** Use SerpApi's `google_maps_reviews` engine to fetch reviews. Drop FireCrawl from the Google Reviews path.

**Why:** Google Maps detects FireCrawl's headless browser and serves a degraded page — 7KB of UI chrome with literal "No reviews" text where reviews should be. Tried direct click on Reviews tab (selector missing on landmarks), JS click on rating chip (modal doesn't render in scraped DOM), search.google.com/local/reviews URL (redirects to SERP without reviews), stealth proxy mode (same degraded result). Burned ~1K FireCrawl credits before concluding.

**Alternatives considered:**
- Apify Google Maps Reviews Scraper ($0.29/1K results) — purpose-built, would work. Dropped for SerpApi because user already had account.
- ScrapingBee / Outscraper / Bright Data — same category as Apify.
- Stay with FireCrawl + accept ~10 reviews/POI — useless signal volume.

**Full details:** `02-firecrawl-pivot.md`

## 2026-06-18 — TDD with vertical slices, no batching

**Decision:** Each behavior gets one RED test → one GREEN implementation. No "write all tests first, then all code."

**Why:** Vertical slices proved themselves during the FireCrawl → SerpApi pivot. Slices 1–4 (URL builder, parser, dedup, save) were source-agnostic and survived the upstream-source swap unchanged. Only fetch_reviews (slice 5) had to be rewritten. If we'd written all tests first against a FireCrawl-shaped API, all of them would have been wrong.

**See:** `05-tdd-plan.md` for the original slice breakdown.

## 2026-06-18 — Default Google Reviews sort modes: newest, lowest, highest (not most_relevant)

**Decision:** `main.py` defaults `GOOGLE_SORT_MODES=newest,lowest,highest`. Not `most_relevant`.

**Why:** The first full run already pulled 200 reviews per POI under `most_relevant`. The cursors for those streams weren't saved (cursor table didn't exist yet), so re-running `most_relevant` would start from page 1 and waste ~66 calls re-fetching content already in the DB. The three new sorts cover genuinely different reviews — `newest` for recency, `lowest` for complaints/scams (highest marginal value), `highest` for the gushing-5-star tail.

**Future:** If we want to grow `most_relevant` past 200, fastest path is to bump `CALLS_PER_STREAM` and let dedup-on-insert silently skip the first 200 — ~11 wasted calls per POI but cheaper than building a "skip-to-page-N" feature.

## 2026-06-18 — Cursor-based resume via `google_reviews_cursors` table

**Decision:** Pagination state persists in DB per `(poi_id, sort_mode)`. Every successful SerpApi call saves the new `next_page_token` before doing anything else.

**Why:** Free-tier SerpApi (250/mo) means we must drain quota across multiple script invocations (cron-style, manual, whatever). Without state, every invocation restarts from page 1 — useless. The cursor table lets each `python main.py` run be a thin slice that picks up exactly where the previous one stopped, with zero re-fetching within a token sequence.

**Subtle property:** SerpApi `next_page_token` may or may not be account-bound (untested). The cursor design works correctly either way — if the token is rejected on account swap, SerpApi returns an error, the script catches it, the cursor stays where it is, and a manual reset would re-fetch but UNIQUE constraint dedups inserts. Worst case is wasted calls, never duplicate rows.

## 2026-06-18 — Module-level throttle (not per-function)

**Decision:** `scrapers/google_reviews.py:_throttle()` uses a module-level `_LAST_REQUEST_AT` global. Every SerpApi call goes through it.

**Why:** First attempt put the sleep inside `fetch_reviews`'s pagination loop. That worked for `max_calls > 1` but broke at `max_calls=1` (our default) because the loop exits before hitting the sleep. The orchestrator then called `fetch_reviews` 3 times back-to-back (one per sort_mode) — 3 SerpApi calls in ~60 seconds, 1080 calls/hour rate, way over the 50/hour cap.

The global-state approach is ugly but correct. Every call site is throttled regardless of who initiated it. Tests override `page_pause_seconds=1` to keep them fast (the throttle still applies but with a 1s floor).

**Alternative considered:** Pass last-call time as a parameter through the call chain. Cleaner functionally but requires plumbing through 3 layers. Rejected because this is a CLI tool, not a library.

## 2026-06-18 — Real Supabase in tests, no mocks

**Decision:** Save/cursor tests round-trip through a real Supabase instance using a `__TEST_POI__` fixture row that's created in setup and deleted in teardown.

**Why:** Mocks would have masked the migration bug that caused the first upsert to fail (`42P10: no unique constraint`). A real round-trip caught it on the first run. The cost (a few hundred ms per test, requires `SUPABASE_SERVICE_KEY` env var) is worth it for a hackathon-scope project. For library code with strict CI portability requirements, this would be different — but we're not that.

**See:** the `test_poi` fixture in `tests/conftest.py:35`.

## 2026-06-18 — Aliases removed from YouTube scraper

**Decision:** Despite the `pois.aliases text[]` column existing, the YouTube scraper does **not** use aliases when generating query templates.

**Why:** First implementation included `Iron Lady` as an alias for Eiffel Tower (it's a French nickname). The search pulled in Margaret Thatcher documentaries and leadership-training videos — irrelevant noise. Single-name search produced cleaner results. Column kept for future use (e.g., extraction-layer disambiguation) but not used at scrape time.

**See:** `scrapers/youtube.py:scrape_poi_youtube` — `all_terms = [poi_name]  # aliases removed`.

## 2026-06-18 — Python 3.14, not 3.9 (the system default)

**Decision:** Use `/opt/homebrew/bin/python3.14` for the venv.

**Why:** `yt-dlp >= 2025.10` requires Python ≥ 3.10. Initial setup on system Python 3.9 hit immediate import errors on the `X | None` union syntax in modern yt-dlp. Bumped to 3.14 (Homebrew current).

**Side effect:** also unblocks PEP 604 union syntax in our own code, but we opted to stay on `Optional[X]` from `typing` for grep-ability and forward compat with older Python if ever needed.

## 2026-06-18 — YouTube: flat-extract for search, full extract for shortlist

**Decision:** Two-phase yt-dlp pattern. `extract_flat=True` for the per-query 10-result discovery phase (cheap, just metadata). Full `extract_info` (slow, resolves formats + subs + comments) only for the top 40 shortlisted videos.

**Why:** Full extraction failed on many videos with "Requested format is not available" until we upgraded yt-dlp. Flat extraction never resolves formats, so the discovery phase is safe even on borderline-broken video IDs. Cost saving: 60+ candidate videos × full extract = lots of unnecessary work.

**See:** `scrapers/youtube.py:_search_videos` (flat) vs `_fetch_full_info` (full).

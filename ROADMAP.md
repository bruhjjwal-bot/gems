# Roadmap

What's built, what's next, and the open questions any contributor should know about.

## Built

- **YouTube scraper** — 106 videos, 101 transcripts, ~7,300 comments across 6 POIs. Self-contained, idempotent, not currently re-run on each invocation.
- **Google Reviews scraper** — SerpApi-backed, 4 sort modes supported, cursor-based resume, rate-limited at 50/hr cap. ~1,200 reviews on `most_relevant` sort, expanding across other sorts.
- **Database schema** — pois, youtube_*, google_reviews, google_reviews_cursors. Migrations versioned in `supabase/migrations/`.
- **Test suite** — 5 fast tests (URL builder, parser, dedup, save round-trip, cursor round-trip) + 3 live tests. Fast suite runs in ~2s against real Supabase.

## Next

In rough priority order. Each item is roughly the size of "the Google Reviews scraper" — a few hours of focused TDD work.

### 1. Reddit scraper

Different shape of signal: forum discussion, deeper context, smaller volume than reviews. FireCrawl works well on reddit.com (no bot wall — see `context/firecrawl-pivot.md`).

Approach:
- Search `site:reddit.com {poi_name} review` via Google search or directly via Reddit's old JSON endpoint (`old.reddit.com/search.json?q=...`).
- Scrape top N threads — title, body, comments, scores.
- Tables: `reddit_threads`, `reddit_comments`.

### 2. TripAdvisor scraper

Highest-volume travel-specific review corpus. Apify has a battle-tested TripAdvisor scraper at low cost (~$0.50 per place fully drained). Or build via FireCrawl on the public review pages — TripAdvisor doesn't bot-detect anywhere near as aggressively as Google Maps.

### 3. Extraction / insight layer

The actual product. Takes raw reviews + transcripts + comments and produces:
- Topic clusters per POI ("crowds", "lines", "photo spots", "scams to watch")
- Sentiment per topic
- Quotable highlights (representative reviews per topic)
- Comparative claims ("Eiffel Tower's lines are reported as worse than Versailles' — sample size N")

Pattern: per-POI batched LLM calls with structured output (Pydantic schema). Caching at the (poi, topic, source_snapshot_hash) level so a re-run on a new review batch only touches the new content.

### 4. Ranking layer

Decide which POIs to surface for a given user query. Uses extraction output + Headout product catalog. Out of scope for the scraper repo.

## Open questions / decisions

- **RLS** — disabled on all tables. Need to enable + write policies before this serves anything beyond local dev. The Supabase advisor flags this on every `list_tables` call.
- **Throttling at the source-level vs global level** — the `_throttle` is global per Python process. If you ever parallelize POI processing (multiprocessing), the throttle won't transfer; you'd need a file-lock or shared timestamp file.
- **Transcript full_text** — currently we store `segments_json` (raw yt-dlp subtitle data) but leave `full_text` NULL. Easy follow-up: post-process segments → concatenated full_text for LLM ingestion.
- **Most_relevant sort_mode is saturated** — already pulled ~200 reviews per POI. Default has been swapped to `newest, lowest, highest`. If you want to grow `most_relevant`, the cursor is currently null (never started using the cursor system) so the next call starts from page 1 and re-fetches everything before producing new content. ~66 wasted calls across 6 POIs to drive `most_relevant` past page 200. Probably not worth it — the other sorts cover more ground.
- **Multi-account quota stretching** — SerpApi's ToS likely forbids creating multiple free accounts. For sustained scraping, upgrade to the $25/mo Starter tier (1,000 calls/mo). The cursor table already enables clean account swaps if you go that route — tokens may or may not be account-bound (untested; would take 1 cheap experiment to verify).

## Known issues

- **SerpApi ReadTimeout** — frequent enough that the script now catches it and bails the stream gracefully (cursor preserved). Each timeout costs you 1 call's worth of progress per stream per occurrence. If outages compound, pause runs for an hour and resume.
- **`.env.example`** — was previously committed with real production keys (a security leak). Now uses placeholders. **Rotate the Supabase service_role JWT** if anyone you don't trust has touched this repo before this fix.
- **Python's stdout buffering when log-redirected** — easy footgun. Always use `python -u` for long runs. Already documented in RUNBOOK.md.
- **No retry/backoff on Supabase errors** — if Supabase has an outage, the script crashes. Not a hot issue but worth knowing.

## Non-goals

- Real-time / streaming review ingestion. This is batch.
- Multi-language review filtering. Currently we just pass `hl=en` to SerpApi; some reviews will be in mixed languages. The extraction layer can translate.
- Sentiment analysis at scrape time. Reviews are raw input to the extraction layer; we don't pre-tag them.
- Image/photo ingestion. SerpApi returns review image URLs; we currently ignore them. Could add an `images jsonb` column to `google_reviews` if needed downstream.

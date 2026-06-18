# Architecture

## Purpose

The product is a **POI intelligence engine**, not the scraper. The scraper is the upstream feed. Its only job: deposit honest, deduplicated visitor signal into Supabase so the extraction/insight layer (not yet built) can do its work.

## Data model

```
pois  ─┬─►  youtube_videos  ─┬─►  youtube_transcripts
       │                     └─►  youtube_comments
       │
       └─►  google_reviews
       │
       └─►  google_reviews_cursors   (pagination state for resumable scraping)
```

### Tables

| Table | Purpose | Notable columns | Idempotency key |
|---|---|---|---|
| `pois` | One row per attraction | `name, city, country, aliases[], category, place_id` | `id` (uuid) |
| `youtube_videos` | Discovered videos | `video_id` (YT id), `format` (short/long), `score` (float; views×0.5 + engagement×0.3 + comments×0.2) | `video_id` |
| `youtube_transcripts` | One per video | `language, segments_json` (raw yt-dlp subs), `full_text` (currently NULL) | `video_id` |
| `youtube_comments` | Top 100 per video | `comment_id, author, text, likes, published_at` | `comment_id` |
| `google_reviews` | Reviews per POI per sort mode | `review_id, author, rating, text, relative_time, iso_date, likes, sort_mode` | `review_id` |
| `google_reviews_cursors` | Resume state | `poi_id, sort_mode, next_page_token, exhausted` | `(poi_id, sort_mode)` |

All scrapers UPSERT on the idempotency key — re-running is always safe.

**RLS is disabled on all tables.** Single-user hackathon scope. Surface the existing Supabase advisory before this leaves the laptop.

## Pipeline flow

### YouTube (`scrapers/youtube.py` — complete, not actively re-run)

1. For each POI, expand into 4 query templates (`{} tips`, `{} visit guide`, `{} review`, `{} itinerary`).
2. For each query, `yt_dlp.YoutubeDL` with `extract_flat=True` returns ~10 video metadata entries — cheap, no format resolution.
3. Dedupe by `video_id`; filter (views ≥ 10K for long-form, 5K for shorts; duration ≥ 30s); score; take top 40.
4. For each shortlisted video, full `extract_info` (no flat) to pull subtitles + top 100 comments via `getcomments`.
5. Upsert into 3 tables. Transcripts stored as raw segments JSON (timestamps preserved).

Idempotency: `youtube_videos.video_id`, `youtube_transcripts.video_id`, `youtube_comments.comment_id` all UNIQUE.

### Google Reviews (`scrapers/google_reviews.py` — active)

Per-run flow (one `python main.py` invocation):

1. Read all POIs with `place_id` set.
2. For each POI, for each sort_mode in `(newest, lowest, highest)` (configurable via `GOOGLE_SORT_MODES` env var):
   - Read cursor row from `google_reviews_cursors` (poi, sort_mode). If `exhausted`, skip.
   - Call SerpApi `google_maps_reviews` engine with `start_token=cursor.next_page_token` (or None for first call), `max_calls=CALLS_PER_STREAM`.
   - Parse SerpApi reviews → normalized `Review` TypedDict (author from `user.name`, text from `snippet`, etc.).
   - Upsert to `google_reviews` (UNIQUE on `review_id` handles cross-sort dedup at insert time).
   - Save new cursor: `next_page_token` from latest response, `exhausted=True` if SerpApi returned no further pages.

Why these 3 sort modes (not `most_relevant`): the initial pass already saturated `most_relevant` to 200 reviews per POI. The other sorts surface genuinely different reviews — `newest` for recency signal, `lowest` for complaints/scams/crowd warnings (highest marginal value), `highest` for the gushing-5-stars tail.

## Throttling

SerpApi free tier is **50 calls per hour** (rolling window). One run does 6 POIs × 3 sort modes = 18 calls. Naively that's ~60 sec of bursting — way over the cap.

**Solution:** a module-level `_throttle(min_interval)` in `scrapers/google_reviews.py:32` that tracks the last-call timestamp across the whole process and sleeps as needed before each request. Default interval = 80s, giving ~45 calls/hour effective — under the cap with headroom.

`page_pause_seconds` flows through `fetch_reviews()` and `scrape_poi_google_reviews()`. Tests override to 1s.

## Pagination & resume

SerpApi pagination is token-based (`next_page_token`), not offset-based. A token has a session-style suffix indicating its position; passing it back fetches the next page. Two important properties:

- **Within a token sequence:** zero overlap. Each call returns a strictly-disjoint page from the last.
- **Across script runs:** without persistence, a new run starts from page 1 and re-fetches everything. The `google_reviews_cursors` table solves this — every successful call updates the cursor before any further work proceeds.

A `(poi, sort_mode)` stream is "exhausted" when SerpApi returns no `next_page_token` or zero reviews on a paginated call. The cursor row's `exhausted=true` flag short-circuits future runs for that stream.

**Failure mode:** if a SerpApi call times out mid-stream, `fetch_reviews` catches `ReadTimeout`/`ConnectionError`, prints a notice, and exits the inner loop without saving a new cursor for the failed page. Next run retries from the same cursor. No data loss.

## TDD strategy

All slices built RED → GREEN one at a time. No batch test-writing. See `context/tdd-plan.md` for the original slice breakdown.

**Test categories:**
- **Fast** (default): URL builder, parser (against recorded SerpApi fixture), dedup, save round-trip against real Supabase (using a `__TEST_POI__` row that's cleaned up in teardown), cursor round-trip. Total runtime: ~2 seconds.
- **Live** (gated behind `-m live`): exercises the real SerpApi pagination + cursor flow. Costs 4-5 calls from the free-tier budget.

**No mocks for FireCrawl/SerpApi/Supabase.** Mock-based tests would have masked the FireCrawl→SerpApi pivot (see `context/firecrawl-pivot.md`). Recorded fixtures + real round-trips keep tests honest about real behavior.

## External services

| Service | Auth | Used for | Doc |
|---|---|---|---|
| Supabase | `SUPABASE_SERVICE_KEY` (server-side service_role) | Postgres + REST API | https://supabase.com/dashboard/project/aycptyqsculzfpzzfsve |
| SerpApi | `SERPAPI_KEY` | Google Maps reviews (free tier: 250/mo, 50/hr) | https://serpapi.com/google-maps-reviews-api |
| yt-dlp | none | YouTube search + transcripts + comments | https://github.com/yt-dlp/yt-dlp |
| FireCrawl | `FIRECRAWL_API_KEY` | **Tried, abandoned for Google Maps** (see context/firecrawl-pivot.md). Still useful for sites without bot detection (Reddit, etc.) | https://docs.firecrawl.dev |

## Extension points

- **New POI:** insert row in `pois` with `place_id` populated. The scraper picks it up on next run automatically.
- **New review source (e.g. Reddit, TripAdvisor):** new module under `scrapers/`, mirroring `scrapers/google_reviews.py`'s shape (fetch → parse → dedup → save). Add a new table with a UNIQUE constraint on the source's stable item ID. Wire into `main.py`.
- **New sort mode:** SerpApi supports `qualityScore | newestFirst | ratingHigh | ratingLow` — they map via `SORT_BY` dict at `scrapers/google_reviews.py:24`.
- **Change throttle:** `PAUSE_SECONDS` env var, default 80. Lower at your own risk against the 50/hr cap.
- **Bigger chunks per script invocation:** `CALLS_PER_STREAM` env var, default 1. Bumping to 2 doubles per-run time but halves number of cron triggers.

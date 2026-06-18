# External service limits & quotas

What each upstream service caps us at, and the math around it.

## SerpApi free tier

- **250 searches per month** — hard cap, no rollover. Resets on billing-cycle anniversary.
- **50 throughput per hour** — rolling-window rate cap. Exceeding it returns HTTP 429.
- No daily limit.
- **Each "search"** = one API call. First-page call returns 8 reviews; paginated calls return up to 20 (with `num=20` once a `next_page_token` is supplied).

### What 250 calls/month actually gets you

Mix of 8-review first calls + 20-review paginated calls. Realistic per-stream:
- 1 first call (8 reviews) + N paginated calls (20 each)
- Steady state with N pagination calls per stream: ~`8 + 20×N` reviews

For 6 POIs × 4 sort modes = 24 streams:
- Fill each stream to 1 first-call: 24 calls = 192 reviews
- Add 1 pagination call to each: 48 calls = 192 + 480 = 672 reviews (24 × 28)
- Continue: each round of +1 call/stream adds 24 calls and 480 reviews

So 250 calls ≈ 24 first-calls (24) + ~9 paginated rounds (216 calls) = 24 streams × ~190 reviews = ~4,500 reviews TOTAL across all 4 sort modes. **Less if there's overlap across sort modes** (the UNIQUE constraint dedups inserts but the API call is still paid for).

In practice we've observed roughly 1 unique-review per call after dedup once you push past page 5-10 of any single sort mode — diminishing returns. So realistic ceiling on free tier across the 6 POIs: **~3,000–4,000 distinct reviews**.

### Starter tier ($25/mo)

1,000 calls/month, 200/hr throughput. Comfortably enough to fully drain all 4 sort modes for all 6 POIs (≈ 1,500-2,000 calls to truly exhaust everything Google exposes).

## Google Maps display ceiling

Google itself caps the number of reviews **viewable** per place around **~5,000**, regardless of total review count. The Eiffel Tower has 510K reviews on its Google Maps card but only the most-relevant 5K are reachable via the Maps UI or any tool that scrapes through the UI.

Implication: for ultra-popular places, the per-POI ceiling is ~5K reviews **per sort mode**. Across 4 sorts: theoretically ~20K per POI (with significant overlap). After dedup, realistic ceiling per POI ≈ 8-12K.

## SerpApi `google_maps_reviews` engine specifics

- Required: `engine=google_maps_reviews`, one of `place_id` or `data_id`, `api_key`.
- Sort modes: `qualityScore` (default, "most relevant"), `newestFirst`, `ratingHigh`, `ratingLow`.
- Filtering: `query` (substring filter on review text), `topic_id` (Google-clustered topic filter, mutually exclusive with `query`), `hl` (language code).
- Pagination: `next_page_token` from previous response's `serpapi_pagination.next_page_token`.
- **`num` parameter is REJECTED on the initial call** (returns HTTP 400). Only valid once `next_page_token` is also set. Default returns 8, with `num=20` returns up to 20.

## FireCrawl (kept for reference, not used for Google reviews)

- Free tier: 500 one-time credits.
- Hobby ($16/mo): 5,000 credits.
- Standard ($83/mo): 100,000 credits.
- `scrape` action: 1 credit per page.
- `interact` (browser actions): 2 credits per browser-minute.
- Stealth proxy: 5x credit multiplier.
- **Hard limit: 50 actions per scrape call.** Hit this trying to chain many scrolls.

## Supabase

We're on the free tier, which is more than enough:
- 500 MB database
- 50K monthly active users (not relevant)
- 2 GB egress
- No per-table row limits

Current usage: ~10K rows total. Tiny.

## yt-dlp

No API key, no quota. YouTube doesn't aggressively block yt-dlp searches. The risks are:
- Format-resolution errors (mitigated by flat-extract pattern).
- Cookie-required content (we don't hit this for landmark searches).
- Rate-based soft-blocks if you hammer it (haven't seen at our scale of ~40 videos × 6 POIs).

## Throttle math (Google Reviews)

The 50/hr SerpApi cap translates to ~72-second minimum interval. We use **80 seconds** for safety margin (~45 calls/hour effective).

With `CALLS_PER_STREAM=1`:
- One run = 18 calls (6 POIs × 3 sort modes, default)
- 17 throttled gaps × 80s = 1,360s sleep + ~3-5s × 18 actual request time = **~23 min runtime per run**
- At 8 runs (≈ 144 calls) = ~3 hours of running + ~1 hour of cron sleeps = ~4 hours wall-clock

If you upgrade to Starter ($25/mo): 200/hr cap allows ~18 sec/call throttle, so a run completes in **~5 min** and you can drain 1,000 calls comfortably in a single day.

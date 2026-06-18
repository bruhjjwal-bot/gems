# Google Reviews scraper (FireCrawl) — TDD build

## Context

YouTube scraper is done (106 videos, 7,236 comments across 6 POIs). Next signal source: Google Reviews — first-hand visitor sentiment with much higher volume. User picked FireCrawl, has 20K credits, and wants the scraper built as small, testable vertical slices using TDD (one RED → GREEN cycle at a time).

### Hard realities to surface before building

1. **The "10K per POI" target is not reliably achievable from google.com/maps.** Google Maps UI exposes ~max 5K reviews per place even when the place has 200K+. The official Places API gives only 5. **Realistic ceiling per POI: 1K–5K.** We'll aim high and accept whatever Google exposes — but the target should be "as many as Google will show us," not literally 10K.
2. **Eiffel Tower has no Reviews tab on Maps.** Reviews appear inline on the Overview page only — lower volume, less reliable extraction. We'll handle it with a fallback action chain (no "click Reviews" step).
3. **FireCrawl is general-purpose.** No off-the-shelf "Google reviews" recipe — we drive it via `actions` (click + scroll + wait) and schema-locked JSON extraction.
4. **Cost is not a constraint** (20K credits). Each per-POI scrape ≈ 10–15 credits (1 page + ~5 browser-minutes). All 6 POIs × multiple sort-mode passes is well within budget.

## TDD plan

**Anti-pattern check (from skill):** we are NOT writing all tests first. Each slice is one full RED → GREEN cycle: write one failing test, write minimal code to pass, move on. Slices are ordered so the earliest tracer bullet is independently meaningful and the system grows outward from there.

### Test infrastructure

- `pytest` + `pytest-mock` added to `requirements.txt`
- `tests/` directory at repo root, `tests/__init__.py`, `tests/conftest.py`
- **FireCrawl-call strategy:** record once, replay forever. Live FireCrawl calls are marked `@pytest.mark.live` and skipped by default (`pytest` runs only fast tests). When we do run a live test, we save the raw FireCrawl response JSON into `tests/fixtures/firecrawl/<name>.json` and from then on the parser/dedup/save tests run against that fixture — no credits burned on repeat runs.
- **Supabase-write strategy:** for the save slice, use a dedicated test row (e.g. a fake POI named `__TEST_POI__`) and clean up in a fixture. Real Supabase, real upsert — the project is single-user hackathon scope so we don't need a separate test DB. We're not mocking the database (mocks would let broken upserts pass green).

### Slices, in build order

Each slice is "RED: write one test → GREEN: write minimum code → done." No batching.

#### Slice 1 — URL builder
- **Behavior**: given a Place ID, return the Google Maps URL that opens that place
- **Test** (`test_build_maps_url_contains_place_id`): assert the returned URL contains the Place ID and the `google.com/maps` host
- **Code**: `build_maps_url(place_id: str) -> str` in `scrapers/google_reviews.py`

#### Slice 2 — Review parser
- **Behavior**: given FireCrawl's JSON response, extract a flat list of review dicts with the fields we care about (review_id, author, rating, text, relative_time, likes)
- **Test** (`test_parse_reviews_extracts_all_fields`): load `tests/fixtures/firecrawl/sample_response.json` (hand-crafted at first — we'll replace with a real recording later), assert N reviews parsed and at least one has every expected field
- **Code**: `parse_reviews(firecrawl_response: dict) -> list[Review]` — pure function, no I/O

#### Slice 3 — Dedup
- **Behavior**: given a list of reviews possibly containing duplicates by `review_id`, return a list with each `review_id` appearing once (keeping the first occurrence)
- **Test** (`test_dedupe_reviews_collapses_duplicates`): pass a list with known dups, assert correct count and order
- **Code**: `dedupe_reviews(reviews: list[Review]) -> list[Review]`

#### Slice 4 — Supabase save
- **Behavior**: given a `poi_id` and a list of reviews, upsert all to `google_reviews` table, idempotent on `review_id`
- **Test** (`test_save_reviews_is_idempotent`): insert the same 3 reviews twice; query the DB; assert exactly 3 rows for that POI. Uses the real Supabase client + a fixture-scoped test POI row that's deleted on teardown.
- **Code**: `save_reviews(poi_id: str, reviews: list[Review]) -> int` returns row count saved

#### Slice 5 — FireCrawl scrape wrapper
- **Behavior**: given a Maps URL and a sort mode, call FireCrawl with the right actions and return the parsed JSON response
- **Test** (`test_fetch_reviews_live`, `@pytest.mark.live`): hit a real small POI (e.g. Trevi Fountain), assert >50 reviews come back. On first green run, save the response to `tests/fixtures/firecrawl/sample_response.json` so Slice 2's test gets a real-shape fixture going forward.
- **Code**: `fetch_reviews(maps_url: str, sort_mode: str = "most_relevant") -> dict` — builds the actions array (click Reviews tab → click Sort → click sort_mode → repeated scroll+wait), calls FireCrawl SDK, returns the structured-JSON portion

#### Slice 6 — Per-POI orchestrator
- **Behavior**: end-to-end — fetch reviews for a POI across all 4 sort modes, dedup the union, save to DB
- **Test** (`test_scrape_poi_google_reviews_end_to_end`, `@pytest.mark.live`): run for one POI; assert >100 rows in `google_reviews` for that POI; assert no duplicate `review_id`s in DB
- **Code**: `scrape_poi_google_reviews(poi_id: str, place_id: str)` — composes Slices 1, 5, 2, 3, 4 in a loop over sort modes

#### Slice 7 — Eiffel Tower fallback
- **Behavior**: when the POI has no Reviews tab (Eiffel Tower), use an alternate action chain that scrolls the Overview page instead
- **Test** (`test_fetch_reviews_falls_back_when_no_reviews_tab`, `@pytest.mark.live`): hit Eiffel Tower with the fallback flag; assert >0 reviews
- **Code**: add a `has_reviews_tab: bool` parameter to `fetch_reviews`; when False, skip the tab-click action and rely on inline reviews

Stop here for the first iteration. Once Slice 7 is green we have a working end-to-end scraper for all 6 POIs. Multi-pass sort coverage (Slice 6) already broadens coverage; if yield is still low we add Slice 8 (cookie banner dismissal, language forcing, etc.) as discrete RED→GREEN cycles.

## Files to create/modify

- **`scrapers/google_reviews.py`** *(new)* — all the functions above, mirrors `scrapers/youtube.py`'s shape
- **`tests/__init__.py`**, **`tests/conftest.py`** *(new)* — pytest config, `live` marker registration, test-POI fixture for the save test
- **`tests/test_google_reviews.py`** *(new)* — all the tests above, one per slice, in build order
- **`tests/fixtures/firecrawl/sample_response.json`** *(new)* — hand-crafted at first, replaced with a real recording after Slice 5
- **`main.py`** — add `scrape_poi_google_reviews(poi["id"], poi["place_id"])` to the per-POI loop, gated on `place_id` not being NULL
- **`requirements.txt`** — add `firecrawl-py`, `pydantic>=2`, `pytest`, `pytest-mock`
- **`.env`** and **`.env.example`** — add `FIRECRAWL_API_KEY=`
- **Supabase migration** — add `place_id text` column on `pois`; create `google_reviews` table

## Supabase schema (one-time migration via `apply_migration`)

```sql
ALTER TABLE pois ADD COLUMN place_id text;

CREATE TABLE google_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  review_id text UNIQUE NOT NULL,
  author text,
  rating int CHECK (rating BETWEEN 1 AND 5),
  text text,
  relative_time text,
  likes int DEFAULT 0,
  sort_mode text,
  scraped_at timestamptz DEFAULT now()
);

CREATE INDEX google_reviews_poi_id_idx ON google_reviews(poi_id);
```

`relative_time` stays as text ("2 weeks ago") — Maps doesn't expose absolute timestamps. Parsing is a downstream concern.

## Place ID seeding (one-time, before Slice 5)

Six manual lookups via Google's Place ID Finder (https://developers.google.com/maps/documentation/javascript/examples/places-placeid-finder), then `UPDATE pois SET place_id = '…' WHERE name = '…';`. ~2 minutes total.

## Verification

After each slice: run `pytest tests/test_google_reviews.py -k <slice_test_name>` — must go RED → GREEN before moving on.

After all slices:
1. `pytest tests/` (skipping `live`) — should be all green, fast
2. `pytest -m live tests/` once with real credits — confirms real-FireCrawl path works
3. Run `python main.py` end-to-end
4. Sanity query:
   ```sql
   SELECT p.name, COUNT(*) AS reviews, AVG(rating)::numeric(3,2) AS avg_rating
   FROM google_reviews g JOIN pois p ON g.poi_id = p.id
   GROUP BY p.name ORDER BY reviews DESC;
   ```
5. Verify uniqueness:
   ```sql
   SELECT review_id, COUNT(*) FROM google_reviews GROUP BY review_id HAVING COUNT(*) > 1;
   ```
   Should return 0 rows.

## What I'm NOT doing

- Not polling Google Places API (5-review cap, useless)
- Not building an Apify/SerpApi fallback — single-tool scope for now
- Not parsing `relative_time` into absolute dates — left for extraction stage
- Not enabling RLS on the new table — surfacing the existing critical advisory from Supabase: `pois`, `youtube_videos`, `youtube_transcripts`, `youtube_comments` all have RLS disabled. The new `google_reviews` table follows the same convention (single-user hackathon scope). Worth fixing before this leaves the laptop.
- Not mocking FireCrawl or Supabase in tests — fixtures (recorded JSON) and real DB (with cleanup) keep tests honest about real behavior

# Runbook

## Setup (fresh machine)

```bash
# Python 3.14 (yt-dlp ≥ 2025.10 dropped 3.9)
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Credentials
cp .env.example .env
# Edit .env and fill in:
#   SUPABASE_URL=https://<project>.supabase.co
#   SUPABASE_SERVICE_KEY=<service_role JWT>
#   SERPAPI_KEY=<from serpapi.com/manage-api-key>
#   FIRECRAWL_API_KEY=<optional; only needed for future Reddit/etc.>

# Schema
# Option A: Supabase CLI (preferred)
supabase link --project-ref <ref>
supabase db push

# Option B: manual — paste each file from supabase/migrations/ into the SQL editor in
#           timestamp order. Each is idempotent (uses IF NOT EXISTS where applicable).
```

After migrations, seed POIs:

```sql
-- Already done in the live DB; reproduce in a fresh project with:
INSERT INTO pois (name, city, country, place_id) VALUES
  ('Eiffel Tower',         'Paris', 'France', 'ChIJLU7jZClu5kcR4PcOOO6p3I0'),
  ('Louvre Museum',        'Paris', 'France', 'ChIJD3uTd9hx5kcR1IQvGfr8dbk'),
  ('Palace of Versailles', 'Paris', 'France', 'ChIJdUyx15R95kcRj85ZX8H8OAU'),
  ('Colosseum',            'Rome',  'Italy',  'ChIJrRMgU7ZhLxMRxAOFkC7I8Sg'),
  ('Trevi Fountain',       'Rome',  'Italy',  'ChIJ1UCDJ1NgLxMRtrsCzOHxdvY'),
  ('Vatican Museums',      'Rome',  'Italy',  'ChIJKcGbg2NgLxMRthZkUqDs4M8');
```

## Running

### Single run (one chunk against current cursors)

```bash
.venv/bin/python main.py
```

- Reads cursor state for every (POI, sort_mode) pair
- Uses up to `CALLS_PER_STREAM` SerpApi calls per stream (default 1)
- Default sort modes: `newest, lowest, highest` (not `most_relevant` — already saturated)
- Throttles 80s between calls (~45/hr, safely under SerpApi's 50/hr cap)
- Prints a summary at exit: `Run summary: N SerpApi calls, M review rows upserted.`

Typical: 18 calls × 80s = **~24 min wall clock**, ~300 new reviews per run.

### Configuration via env vars

```bash
GOOGLE_SORT_MODES=newest,lowest .venv/bin/python main.py   # narrow to 2 sorts
CALLS_PER_STREAM=2 .venv/bin/python main.py                # bigger chunks
PAUSE_SECONDS=80 .venv/bin/python main.py                  # default; reduce at your own risk
```

### Multi-run loop (drain remaining quota)

If you have N free-tier calls left and want to use them across the day:

```bash
caffeinate -i bash -c '
  for i in 1 2 3 4 5 6 7 8; do
    echo "=== Run $i $(date) ===" >> ~/gems-run.log
    .venv/bin/python -u main.py >> ~/gems-run.log 2>&1
    echo "=== Run $i done ===" >> ~/gems-run.log
    [ $i -lt 8 ] && sleep 480
  done
' &
```

`-u` makes Python unbuffered so `tail -f ~/gems-run.log` shows progress live. `caffeinate -i` prevents idle sleep for the duration. 8 runs × (24 min run + 8 min sleep) ≈ 4.3 hours.

## Testing

```bash
# Fast tests only (no API costs)
.venv/bin/pytest tests/

# Live tests (uses 4-5 SerpApi calls from your budget)
.venv/bin/pytest tests/ -m live

# Single test by name
.venv/bin/pytest tests/test_google_reviews.py::test_cursor_round_trip -v
```

The fast suite uses a real Supabase round-trip (via the `test_poi` fixture in `tests/conftest.py`) — no mocks. The fixture creates a `__TEST_POI__` row, yields its ID, and deletes the row + its `google_reviews` + `google_reviews_cursors` on teardown.

## Monitoring an active run

```bash
# Live log
tail -f ~/gems-run.log

# Check Python is still alive
ps aux | grep main.py | grep -v grep

# Cursor state (which streams are progressing / exhausted)
# Run in Supabase SQL editor:
SELECT p.name, c.sort_mode, c.exhausted,
       LEFT(c.next_page_token, 24) AS token_preview,
       c.updated_at
FROM google_reviews_cursors c JOIN pois p ON c.poi_id = p.id
ORDER BY c.updated_at DESC;

# Review counts per POI
SELECT p.name, COUNT(*) AS reviews, ROUND(AVG(rating)::numeric, 2) AS avg_rating
FROM google_reviews g JOIN pois p ON g.poi_id = p.id
GROUP BY p.name ORDER BY reviews DESC;

# SerpApi credit balance (replace KEY)
curl -s "https://serpapi.com/account?api_key=$SERPAPI_KEY" | python -m json.tool
```

## Aborting safely

The script saves cursor state AFTER each successful SerpApi call → before any further work. Killing it at any point loses **at most the in-flight call's reviews**, and that stream resumes cleanly from the last good cursor on the next run.

```bash
# Find the bash loop wrapper
ps aux | grep "caffeinate -i bash" | grep -v grep | awk '{print $2}' | xargs kill

# Or the Python child specifically
ps aux | grep main.py | grep -v grep | awk '{print $2}' | xargs kill
```

## Common failures

### `requests.exceptions.ReadTimeout`

SerpApi's `google_maps_reviews` engine sometimes takes 60+ seconds to respond. The current code (after the fix in `scrapers/google_reviews.py`) catches this, prints a notice, and exits the inner loop with the cursor preserved at the last good page. Next run resumes from there.

Timeout is set to 180s in `fetch_reviews`. If you see persistent timeouts, SerpApi is having an outage — pause runs for an hour and try again.

### `KeyError: 'SERPAPI_KEY'` (or any env var)

`load_dotenv()` reads `.env` from the current working directory. Always run `python main.py` from the project root, or set vars explicitly.

### `42P10: there is no unique or exclusion constraint`

Means a migration is missing. Specifically the upsert path needs unique constraints on `review_id` (google_reviews), `video_id` (youtube_transcripts, youtube_videos), `comment_id` (youtube_comments), and `(poi_id, sort_mode)` (google_reviews_cursors). All defined in `supabase/migrations/`.

### Pytest can't find `scrapers.google_reviews`

`tests/conftest.py` prepends the project root to `sys.path`. Make sure pytest is invoked from the project root, not from inside `tests/`.

### Hitting SerpApi rate limit (429)

If you see HTTP 429 in the log, the `_throttle` interval is too aggressive. Bump `PAUSE_SECONDS` to 90 or 100. The 50/hr cap is a rolling window, so a single burst can trip it even if the long-term average is fine.

## Adding a new POI

1. Find the Google Maps Place ID via https://developers.google.com/maps/documentation/javascript/examples/places-placeid-finder
2. `INSERT INTO pois (name, city, country, place_id) VALUES (...)`
3. Next `python main.py` run automatically picks it up — no code change needed.
4. For YouTube coverage, run `scrape_poi_youtube(...)` manually or uncomment `run_youtube(pois)` in `main.py`.

## Deploying / scheduling

We don't deploy this — it's a one-off scraper. If you want recurring runs, cron is fine:

```cron
*/30 * * * * cd /path/to/gems-scraper && /usr/bin/flock -n /tmp/gems.lock .venv/bin/python main.py >> ~/gems-run.log 2>&1
```

`flock -n` prevents overlapping invocations if a run goes longer than expected. Remove the cron line once you're done draining the quota.

# Gems Scraper

POI intelligence pipeline for Headout. Mines real visitor signal from YouTube and Google Maps reviews into a structured Supabase database, so a downstream extraction layer can rank attractions, surface hidden tips, and answer "what's it actually like?" questions about a place.

**Current scope:** 6 European landmark POIs (Paris + Rome) — Eiffel Tower, Louvre Museum, Palace of Versailles, Colosseum, Trevi Fountain, Vatican Museums.

**What's built:**
- `scrapers/youtube.py` — yt-dlp based; searches 4 query templates per POI, filters by views + engagement, fetches transcripts + top 100 comments per shortlisted video.
- `scrapers/google_reviews.py` — SerpApi based; paginated across 4 sort modes (most_relevant, newest, highest, lowest) with persistent cursors so script runs resume exactly where the last one stopped.

**What's in the DB (as of last snapshot):** 106 YouTube videos, 101 transcripts, ~7,300 comments, 1,200+ Google reviews (growing).

## Quick start

```bash
# 1. Python 3.14 venv (yt-dlp ≥2025 dropped 3.9)
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, FIRECRAWL_API_KEY, SERPAPI_KEY

# 3. Apply migrations (via Supabase CLI or paste into SQL editor)
#    Files in supabase/migrations/ — run in filename order
supabase db push   # if you have the CLI linked to the project

# 4. Seed POIs and Place IDs (one-time)
#    See context/place-ids.md for the Place IDs we use

# 5. Run
.venv/bin/python main.py
```

## Repository tour

```
gems-scraper/
├── main.py                       # Entry point. Calls run_google_reviews(). YouTube call commented (data already collected).
├── db/client.py                  # Singleton Supabase client.
├── scrapers/
│   ├── youtube.py                # yt-dlp scraper. Done; not actively used.
│   └── google_reviews.py         # SerpApi scraper. Active.
├── tests/
│   ├── conftest.py               # pytest config; `live` marker; test_poi fixture (real Supabase row, auto-cleaned).
│   ├── test_google_reviews.py    # Fast tests (URL builder, parser, dedup, save, cursor) + live tests (gated behind -m live).
│   └── fixtures/serpapi/         # Recorded SerpApi response for replay-only tests.
├── supabase/migrations/          # SQL migrations in chronological order. Apply via `supabase db push` or paste into SQL editor.
├── context/                      # Historical / explanatory docs. Read these to understand WHY decisions were made.
├── ARCHITECTURE.md               # Data model, pipeline flow, throttling design.
├── RUNBOOK.md                    # How to operate: run, test, monitor, abort, debug.
├── ROADMAP.md                    # What's next: Reddit, TripAdvisor, extraction layer.
├── requirements.txt
├── .env.example                  # Template. Real .env is gitignored.
└── .gitignore
```

## Testing

```bash
# Fast tests only (no API calls, real Supabase round-trip for save test)
.venv/bin/pytest tests/

# Live tests (hit SerpApi — costs 4-5 free-tier calls)
.venv/bin/pytest tests/ -m live
```

See **ARCHITECTURE.md** for the system design, **RUNBOOK.md** for operational details, **ROADMAP.md** for next steps, and **context/** for the decisions and pivots behind the current design.

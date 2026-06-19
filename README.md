# Gems — POI Intelligence Engine

> Real visitor signal, structured at scale. Built for the Headout hackathon.

Gems mines first-person visitor experiences from Google Reviews, TripAdvisor, Reddit, and YouTube — extracts structured insights, clusters them by theme, ranks them by signal quality, and exposes them through a RAG-powered chat API and MCP server.

**Live demo:** `https://signature-happy-dean-mixing.trycloudflare.com`
**Current scope:** Colosseum + Louvre Museum (hackathon sprint)

---

## The Problem

Generic travel content (SEO articles, LLM answers) all regresses to the same common knowledge. The real signal lives in Reddit threads, Google Reviews, and YouTube comments — tens of thousands of first-person accounts saying things like *"the underground arena tour is worth it but book 3 days ahead"* or *"scammers near the south entrance target solo travellers."*

Gems turns that raw signal into a structured, queryable intelligence layer.

---

## Architecture

```
Raw Signal Sources
  ├── Google Reviews    (SerpApi + Apify compass/Google-Maps-Reviews-Scraper)
  ├── TripAdvisor       (FireCrawl stealth scrape)
  ├── Reddit            (FireCrawl discovery + Apify harshmaur/reddit-scraper)
  └── YouTube           (yt-dlp: transcripts + comments)
          │
          ▼
  Supabase (raw storage — deduplicated, provenance-tracked, reprocessable)
          │
          ▼
  Pipeline (l2/)
  ├── extract.py        — LLM structured extraction per source type
  ├── cluster_all.py    — semantic clustering of similar insights
  ├── score_all.py      — rank clusters by frequency/recency/specificity
  ├── label_all.py      — LLM taxonomy tagging (l1/l2 intent categories)
  └── build_index.py    — embed cluster names → numpy index for retrieval
          │
          ▼
  Intelligence Store (l2/data/)
  ├── flat_insights_enriched.json   — ~4,600 structured insight rows
  ├── clusters_all.json             — ~1,450 ranked topic clusters
  ├── cluster_embeddings.npy        — vector index for semantic search
  └── insight_embeddings.npy        — per-insight vectors
          │
          ▼
  Serving Layer
  ├── l2/api_server.py    — Flask REST API (6 endpoints)
  ├── l2/ask_gems.py      — RAG agent (OpenAI tool-use loop)
  └── l2/mcp_server/      — MCP server for Claude/AI agent consumption
```

---

## Repository Map

```
gems-scraper/
│
├── scrapers/                     # Data ingestion layer
│   ├── google_reviews.py         # SerpApi Google Maps reviews (cursor-based pagination)
│   ├── google_reviews_apify.py   # Apify enrichment (2k most-relevant + 1k high/low per POI)
│   ├── tripadvisor.py            # FireCrawl stealth scrape + multi-key rotation
│   ├── reddit.py                 # FireCrawl discovery + Apify enrichment (13 intent categories)
│   └── youtube.py                # yt-dlp search, transcripts, top-100 comments
│
├── l2/                           # Intelligence pipeline + serving
│   ├── extract.py                # Structured extraction (insight, sentiment, entity, tags)
│   ├── cluster_all.py            # Semantic clustering per POI + source
│   ├── score_all.py              # Signal quality ranking (frequency × recency × specificity)
│   ├── label_all.py              # Taxonomy tagging: 5 l1 intents × 14 l2 sub-intents
│   ├── build_index.py            # Embed cluster names → numpy retrieval index
│   ├── ask_gems.py               # RAG agent: OpenAI tool-use loop over retrieval functions
│   ├── api_server.py             # Flask REST API (6 endpoints, CORS-open)
│   ├── chat.html                 # Chat demo UI (served at GET /)
│   ├── mcp_server/
│   │   ├── server.py             # MCP server entry point
│   │   ├── retrieval.py          # search(), search_insights(), list_highlights()
│   │   ├── evidence.py           # explain() — cluster → raw reviewer quotes
│   │   └── store.py              # In-memory store loader (clusters + insights + embeddings)
│   └── data/                     # Pre-built intelligence store (committed, ~59MB)
│       ├── flat_insights_enriched.json
│       ├── clusters_all.json
│       ├── cluster_embeddings.npy
│       └── insight_embeddings.npy
│
├── db/
│   └── client.py                 # Supabase singleton client
│
├── supabase/migrations/          # Schema history (ordered, idempotent)
│   ├── 20260618144045_create_pois_and_youtube_tables.sql
│   ├── 20260618160222_add_google_reviews.sql
│   ├── 20260619000000_add_tripadvisor_reviews.sql
│   ├── 20260619100000_add_reddit_tables.sql
│   └── 20260619200000_add_google_reviews_apify_columns.sql
│
├── tests/                        # TDD test suite (fast + live tests per scraper)
│   ├── conftest.py               # test_poi fixture (real Supabase, auto-cleaned)
│   ├── test_google_reviews.py
│   ├── test_google_reviews_apify.py
│   ├── test_tripadvisor.py
│   ├── test_reddit.py
│   └── fixtures/                 # Recorded API responses for offline unit tests
│
├── scripts/                      # One-shot enrichment scripts
│   ├── enrich_google_apify.py    # Apify Google Maps Reviews enrichment run
│   └── smoke_reddit_apify.py     # Reddit Apify smoke test (mode A vs B comparison)
│
├── context/                      # Decision docs
│   ├── 01-decisions.md           # Key architectural decisions + rationale
│   ├── 02-firecrawl-pivot.md     # Why we moved from FireCrawl to SerpApi for Google
│   └── 03-limits-quotas.md       # API rate limits, quotas, cost tracking
│
├── main.py                       # Scraper runner (SCRAPERS= env var)
├── ARCHITECTURE.md               # Scraper data model + pipeline flow deep-dive
├── ROADMAP.md                    # What's built, what's next, open questions
└── DEPLOY.md                     # Cloudflare Tunnel + Railway deploy instructions
```

---

## Data in Supabase

| Table | Rows | Description |
|---|---|---|
| `pois` | 6 | Attraction entity records with aliases and place_id |
| `google_reviews` | ~5,800 | SerpApi + Apify (mostRelevant / highest / lowest sorts) |
| `tripadvisor_reviews` | ~8,000 | FireCrawl stealth scrape |
| `reddit_posts` | ~218 | FireCrawl discovered + Apify enriched threads |
| `reddit_comments` | ~3,781 | With intent-inherited provenance links |
| `youtube_videos` | 106 | Top-scored videos per POI |
| `youtube_comments` | ~7,300 | Top-100 comments per video |
| `youtube_transcripts` | 101 | Raw subtitle segments |

All raw items stored forever — enables reprocessing with better models.

---

## Intelligence Store

Pre-built pipeline output committed in `l2/data/`. The API loads at startup — no DB connection needed for serving.

**Insight taxonomy:**

| l1 | Example l2 sub-intents |
|---|---|
| Visit Intelligence | Practical Visit Tip, Crowd Flow Insight, Timing Advice, Route Tip |
| Attention Intelligence | Hidden Highlight, Regret Signal, Warning |
| Discovery Intelligence | Sub-Attraction Highlight, Underrated Area |
| Culinary Intelligence | Restaurant Recommendation, Food Market |
| Operational Intelligence | Ticket Strategy, Accessibility |

---

## API Reference

### `GET /` — Chat UI
Interactive demo. Select Colosseum or Louvre, ask a question, get a cited answer.

### `GET /api/health`
```json
{"status":"ok","clusters":1450,"insights":4622,"pois":["Colosseum","Louvre Museum"]}
```

### `POST /api/ask` — RAG answer with citations
```bash
curl -X POST /api/ask -H "Content-Type: application/json" \
  -d '{"query": "Is the underground arena tour worth it?", "poi": "Colosseum"}'
```
Returns: `answer`, `citations[]` (source + raw quote + cluster_id), `confidence`.

### `POST /api/search_clusters` — Semantic cluster search (~200ms, no LLM)
```bash
curl -X POST /api/search_clusters -H "Content-Type: application/json" \
  -d '{"poi": "Louvre Museum", "query": "avoiding queues", "limit": 5}'
```

### `POST /api/search_insights` — Filtered insight search
Filters: `source`, `rating_min/max`, `has_numeric`, `insight_sentiment`, `sub_attraction`, `intent_tag`.

### `POST /api/explain_cluster` — Raw reviewer quotes for a cluster
```bash
curl -X POST /api/explain_cluster -H "Content-Type: application/json" \
  -d '{"cluster_id": 3, "max_quotes": 5}'
```

---

## Key Design Decisions

**Why store raw data forever?** Extraction models improve. Every row has a stable idempotency key (`review_id`, `reddit_id`, `video_id`) so reprocessing is free — no re-scraping.

**Why multiple sort modes for Google Reviews?** `most_relevant` saturates at ~200 rows. `lowest` sort surfaces complaint/scam signal with the highest marginal extraction value. `highest` captures the gushing 5-star tail.

**Why FireCrawl + Apify for Reddit (not the Reddit API)?** FireCrawl's Google-backed search has much better recall across older threads. Apify enriches the full thread with 60+ structured fields including engagement metrics and comment trees.

**Why cluster before ranking?** The same insight gets said 50 different ways across 50 reviews. Clustering collapses those into one signal with a frequency count — ranking by `frequency × recency × specificity × cross-source presence` surfaces reliable advice, not just repeated phrasing.

**Why MCP + REST API?** MCP serves AI agents (Claude, Cursor) that need tool-based retrieval. REST serves browser UIs and teammate integrations without MCP plumbing.

---

## Running Locally

```bash
# 1. Python 3.14 venv
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Required for serving: OPENAI_API_KEY
# Required for scraping: SUPABASE_URL, SUPABASE_SERVICE_KEY, SERPAPI_KEY, FIRECRAWL_API_KEY, APIFY_TOKEN

# 3. Start the API (intelligence store loads from l2/data/ — no DB needed)
python -m l2.api_server
# → http://localhost:8000  (chat UI at /)

# 4. Run the fast test suite (~2 seconds)
python -m pytest tests/ -v
```

---

## What This Isn't

- Not a recommendation engine — designed for visit-maximisation for people who already booked
- Not real-time — batch pipeline, weekly refresh cadence
- Not multilingual — English signal only

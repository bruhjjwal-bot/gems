# Gems API — Deploy & Teammate Quickstart

Two ways to get a public HTTPS URL for the Flask API.

## Option 1 — Cloudflare Tunnel (instant, no account, ephemeral)

Best for "teammate needs to start hitting it NOW while you set up Railway". URL changes per session, depends on your laptop staying on.

```bash
# One-time install
brew install cloudflared

# Start the API locally (terminal A)
cd /Users/headout/Documents/Gems/gems-scraper
set -a && source .env && set +a
.venv/bin/python -m l2.api_server

# Expose it publicly (terminal B)
cloudflared tunnel --url http://localhost:8000
# → prints a URL like https://abc-def-ghi.trycloudflare.com
```

Hand that URL to your teammate. They append `/api/health` to verify.

## Option 2 — Railway (stable, ~$0 for the hackathon)

Stable HTTPS URL, survives laptop close, $5/mo free credit covers a weekend easily.

### Pre-flight check (already done)

Files prepped in the repo root:
- `Procfile` — gunicorn start command
- `nixpacks.toml` — Python 3.11 + only API deps
- `requirements_api.txt` — Flask + gunicorn + openai + numpy + mcp (no pipeline cruft)
- `runtime.txt` — fallback Python version pin
- `.railwayignore` — excludes pipeline scripts, raw data we don't use at runtime

Total uploaded size: **~50 MB** (data files including embeddings + raw quotes for evidence).

### Deploy steps

```bash
# 1. Install Railway CLI (one-time)
brew install railway

# 2. Login (opens a browser tab)
railway login

# 3. From the repo root, init a new project
cd /Users/headout/Documents/Gems/gems-scraper
railway init   # name it e.g. "gems-poi"

# 4. Set the OpenAI key as an env var (pull from your local .env)
railway variables --set OPENAI_API_KEY="$(grep OPENAI_API_KEY .env | cut -d= -f2)"
# or paste it manually:
# railway variables --set OPENAI_API_KEY=sk-proj-...

# 5. Deploy
railway up

# 6. Generate a public URL
railway domain
# → https://gems-poi-production.up.railway.app
```

Smoke-test once it's live:

```bash
curl https://<your-url>/api/health
# {"status":"ok","clusters":1450,"insights":4622,"pois":["Colosseum","Louvre Museum"]}
```

If the build fails, `railway logs` tails the deploy log.

---

# Teammate API Reference

Base URL: whatever the public URL is (`https://abc.trycloudflare.com` or `https://gems-poi-production.up.railway.app`).

CORS is wide open — call directly from a browser.

## Endpoints

### `GET /api/health`
Liveness + store stats.

```bash
curl $BASE/api/health
```

```json
{
  "status": "ok",
  "clusters": 1450,
  "insights": 4622,
  "pois": ["Colosseum", "Louvre Museum"],
  "last_built": "2026-06-19T20:15:22"
}
```

### `GET /api/pois`
List supported POIs with summary metadata.

```bash
curl $BASE/api/pois
```

```json
[
  {
    "poi_name": "Colosseum",
    "total_clusters": 687,
    "total_insights": 2074,
    "tier_a_count": 143,
    "sources": ["google_review", "reddit_comment", "reddit_post", "tripadvisor_review", "youtube_transcript_chunk"],
    "last_built": "2026-06-19T20:15:22"
  },
  ...
]
```

### `POST /api/ask` — main RAG endpoint
LLM-synthesised answer with cited evidence. Takes 2–6 seconds (LLM + retrieval). Best for chat UI.

```bash
curl -X POST $BASE/api/ask -H "Content-Type: application/json" -d '{
  "query": "Should I get the Roma Pass?",
  "poi": "Colosseum",
  "booking_context": "user has 10am Colosseum tour tomorrow"
}'
```

Response:
```json
{
  "answer": "The Roma Pass offers unlimited transit + free entry to attractions including the Colosseum…",
  "citations": [
    {
      "source": "youtube_transcript_chunk",
      "source_id": "...",
      "quote": "So earlier Brady and Casey went and picked up our Roma passes…",
      "cluster_id": 250,
      "cluster_name": "Roma Pass is recommended for…",
      "rating": null
    }
  ],
  "supporting_clusters": [...],
  "confidence": "high",
  "caveats": "optional — surfaced when evidence is thin",
  "query": "Should I get the Roma Pass?",
  "tool_calls_log": [...]
}
```

Fields:
- `confidence`: `"high" | "medium" | "low"` — `low` means <2 citations or weak match
- `citations[]`: ≥2 required when confidence is high; each has source platform + raw quote + cluster lineage

### `POST /api/search_clusters` — tier-1 raw retrieval
Cluster-level semantic search, no LLM. ~200ms after warm. Use for top-K recommendation cards.

```bash
curl -X POST $BASE/api/search_clusters -H "Content-Type: application/json" -d '{
  "poi": "Louvre Museum",
  "query": "how to avoid the queue",
  "intent_l1": "Visit Intelligence",
  "limit": 5,
  "min_tier": "B"
}'
```

Response: array of cluster cards. `intent_l1` and `min_tier` are optional.

`intent_l1` values: `"Visit Intelligence"`, `"Attention Intelligence"`, `"Discovery Intelligence"`, `"Culinary Intelligence"`, `"Operational Intelligence"`.

`min_tier` values: `"A"` (highest quality), `"B"` (default), `"C"` (include everything).

### `POST /api/search_insights` — tier-2 raw retrieval
Per-row insight search with structured filters. Use when you need exact quotes / numeric anchors / source-filtered results.

```bash
curl -X POST $BASE/api/search_insights -H "Content-Type: application/json" -d '{
  "poi": "Colosseum",
  "query": "entry ticket price",
  "filters": {
    "has_numeric": true,
    "rating_max": 3,
    "source": ["tripadvisor_review", "google_review"]
  },
  "limit": 10
}'
```

Filter keys (all optional, AND-combined):
- `l1`, `l2` — taxonomy match
- `rating_min`, `rating_max` — review rating (1-5)
- `source` — list of platform names
- `insight_sentiment` — list of `["positive", "negative", "neutral", "mixed"]`
- `intent_tag` — list of `["warning", "recommendation", "explanation", "comparison", "history", "description"]`
- `has_numeric` — true → only rows with extracted numeric anchors
- `sub_attraction` — case-insensitive substring (e.g. `"Denon"`)
- `cluster_id` — exact match
- `min_freshness` — float 0..1

### `POST /api/explain_cluster` — citation trail
Given a `cluster_id` (from any search endpoint), return raw reviewer quotes.

```bash
curl -X POST $BASE/api/explain_cluster -H "Content-Type: application/json" -d '{
  "cluster_id": 686,
  "max_quotes": 5
}'
```

Response: `{"cluster": <cluster_card>, "quotes": [{"source", "raw_text", "rating", "source_id", "anchor_entity"}, ...]}`

## Common errors

| Status | When | Fix |
|---|---|---|
| 400 | Missing `query` / `poi` / `cluster_id` | Check request body |
| 400 | `poi` not in `["Louvre Museum", "Colosseum"]` | Use the exact strings |
| 500 | OpenAI API key missing | Server-side env var issue, not client |
| 500 | Cluster embeddings missing | Build step skipped server-side — server admin issue |

## Latency expectations (post-warmup)

| Endpoint | Typical | P99 |
|---|---|---|
| `/api/health` | 5ms | 20ms |
| `/api/pois` | 10ms | 30ms |
| `/api/search_clusters` | 200ms | 500ms |
| `/api/search_insights` | 200ms | 600ms |
| `/api/explain_cluster` | 30ms | 100ms |
| `/api/ask` (full RAG) | 2-4s | 8s |

Cold start (Railway) adds ~10s on the first request after deploy.

## What's supported

POIs: **Louvre Museum** + **Colosseum** only (hackathon scope).
Languages: English.
History/booking: pass via `booking_context` string in `/api/ask` body.

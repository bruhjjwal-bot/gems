# Items needing your review

_Generated during the autonomous block, 2026-06-19 → 2026-06-20._

---

## What's live right now

### 1. Gems POI Intelligence API + 6 demo apps
**URL:** https://gems-poi-production.up.railway.app

| Slug | App | Stage |
|---|---|---|
| `/` | Editorial landing page (masthead, contents, links to all apps) | — |
| `/citation-mode` | Footnoted briefing where every claim opens a marginalia card with the raw reviewer quote | Pre-visit |
| `/the-verdict` | Magazine-style yes/no jury with a monumental verdict score | Explore |
| `/scam-sheet` | Pickpocket + scam patterns, cited & dated | Pre-visit |
| `/two-tickets` | Louvre vs Colosseum, Wirecutter-style | Explore |
| `/three-hour-cut` | Tell us your window, get a route built from regret + numeric anchors | Plan |
| `/sku-gap-finder` | Sub-attractions Headout doesn't sell tickets to yet | Internal |
| `/chat` | Legacy chat dashboard | Dev |

New API endpoints I added during the build:
- `GET /api/poi_stats?poi=...` — tier/sentiment/intent counts + verdict_score
- `GET /api/sub_attractions?poi=...` — sub_attraction grouping with median minutes + cluster IDs

### 2. Carousel Studio (separate Railway project)
**URL:** https://carousel-studio-production-6430.up.railway.app
**Folder:** `carousel-studio/` — self-contained, Vercel-compatible.

Five-agent pipeline behind a 4-step editorial UI:

1. **Topic Scout** (`/api/topics`) — gpt-4o-mini, grounded by Gems search_clusters
2. **Slide Architect** (`/api/outline`) — gpt-4o, pulls real quotes via explain_cluster
3. **Design Director** (`/api/direction`) — gpt-4o, constrained to pick from `lib/constants.js` (5 palettes × 5 type pairs × 10 layouts) — _this constraint is the entire anti-slop wedge_
4. **Image Prompter** (`/api/prompt-slide`) — gpt-4o-mini, writes the gpt-image-2 prompt per slide
5. **Renderer** (`/api/render-slide`) — gpt-image-2, returns b64

Sample render (low quality, ~$0.01 per slide): `carousel-studio/scripts/smoke-output/slide-0-low.png` — verified editorial, no slop.

---

## Decisions I made on your behalf (your call to redo)

### Image model — `gpt-image-2`
I queried `GET /v1/models` on your account at the start of this block. Latest image model listed was `gpt-image-2` (the April-2026 release), which is what the studio uses. There's also `gpt-image-2-2026-04-21` (a pinned snapshot) and `gpt-image-1.5` (older). If you'd rather pin to `gpt-image-1.5` for cost or consistency, just set `IMAGE_MODEL` in the Railway env vars and redeploy.

### Default quality = `medium`
gpt-image-2 quality tiers cost roughly $0.011 / $0.042 / $0.167 for low/medium/high at 1024×1024. The studio defaults to medium (~$0.04/slide, ~$0.25/carousel) but exposes a UI toggle. **No rate limiting, no auth.** Anyone with the URL can spend your OpenAI balance. If this stays public, add a simple bearer token or rate-limit middleware before sharing widely.

### Curated brand vocabulary
Five palettes (Roman Stone / Cobalt Press / Olive & Maroon / Newsprint / Vatican Plaster), five type pairings (Fraunces+Newsreader / Crimson+IBM Plex / EB Garamond + Inter Tight / etc), ten layouts (silhouette overlay / map pin / pull quote / data number / vs compare / receipt card / polaroid / boarding pass / magazine cover / index card) — all in `carousel-studio/lib/constants.js`. Want a new look? Add to the list, redeploy. **Do not** let the LLM freelance — the constraint IS the differentiator.

### Where the Carousel Studio is deployed
**Railway, separate project (`carousel-studio`)**, not Vercel. The Vercel CLI wasn't installed and you weren't around to log in. The folder structure is Vercel-zero-config compatible — `vercel deploy` from inside `carousel-studio/` will work the moment you install the CLI. The Railway deploy uses a thin Express wrapper (`server.js`) that mounts the same `api/*.js` handlers, so both paths stay in sync.

### Landing page link
I added a "Carousel Studio ↗" entry as item #7 on the gems-poi landing page (`/`). Opens in a new tab to the separate Railway domain. Remove the entry if you'd rather keep them visually separate.

### No persisted carousel storage
Studio state lives in `sessionStorage` only. Close the tab → lose the carousel. Easy to wire up Supabase later (we already have credentials). Held off because none of the prior conversation suggested it was needed for the demo.

### Top-of-funnel apps — `/api/ask` is the LLM-heavy one
The 6 demo apps mostly use the cheap `/api/poi_stats`, `/api/search_clusters`, `/api/search_insights`, `/api/explain_cluster` endpoints. Only `/api/ask` (Citation Mode's "show receipts" button uses it indirectly) burns gpt-4o tokens at ~$0.01/call. Demo traffic should cost basically nothing.

---

## Open items from earlier in the day (deferred, not blocking the demo)

These came from the V3 pipeline rebuild before this productization block — listed so you can decide whether to chase them after the demo:

- **V3 extract phase ended early.** New sources (firecrawl_blog, reddit_targeted) NEVER actually got integrated into the cluster pipeline. Code changes are in place but execution incomplete. The demo runs on V2 clusters + Reddit-targeted-only data.
- **secondary_l1 fire rate** went down (5% → 3.9%) instead of up to the target 10% — prompt change may have raced the deploy.
- **Sperone Valadier** still appears as a size-1 cluster — discard softening didn't propagate to all callsites.
- **Culinary Louvre gap** unfilled — 3 of 3 ingested rows from the culinary blog source were 404 pages, so we kept the Reddit fallback.
- **YouTube background loops** — the 15m pulse-check loop is still firing (logs at `louvre-transcript-refresh.log`, `colosseum-expand.log`). Stop with `CronList` then `CronDelete` if you want to silence it.

---

## 105 ideas from the PM-panel workflow

Full RICE-ranked list is in the workflow output for run `wf_e8d57fb7-5bc`. Top 6 went to build. Top 10–14 not built (saved for v2):

- **The Regret Engine** — reverse-engineer the trip from what visitors wished they'd done
- **Worth-It Index** — single 0–100 score (already partially exposed as `verdict_score`)
- **The Skeptic's Walkthrough** — voice for jaded users
- **Receipts** — marketing internal tool for citation-backed ad copy
- **The Brief** — printable one-pager
- **Pre-Visit Reading Room** — top Reddit threads
- **Did You Miss It?** — post-visit JTBD
- **Mona Lisa Reality Check** — expectation-setting

If you want any of these built tomorrow, the same workflow with a different selection list will produce them in ~30 min.

---

## What I'd ask before another autonomous block

1. Should the Carousel Studio be open or gated? (cost exposure)
2. Should generated carousels be saved server-side (Supabase) or stay client-only?
3. Is the Vercel deploy path worth setting up, or is the Railway-separate-project pattern fine for the demo?
4. Do you want a 6th step (caption + hashtag generation) added to the studio before showing the team?
5. Should the studio include Headout-brand watermarking on rendered slides, or stay neutral?

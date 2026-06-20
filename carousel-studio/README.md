# Carousel Studio

Agentic Instagram-carousel generator grounded in Gems POI insight data. Five agents (Topic Scout → Slide Architect → Design Director → Image Prompter → Renderer) chained behind a four-step editorial studio UI. No glassmorphism, no purple gradients, no AI slop.

Standalone — deploy anywhere that can host a static `public/` folder plus Node.js serverless functions.

## Stack
- Static frontend: single HTML file at `public/index.html`
- Backend: Vercel-style serverless functions in `api/*.js`
- LLM: `gpt-4o` + `gpt-4o-mini` for ideation/structure
- Image: `gpt-image-2` for final renders (in-image type via the new render model)
- Grounding: calls a Gems API instance for cluster/insight evidence; falls back to pure-LLM if unreachable

## Local dev

```bash
cd carousel-studio
cp .env.example .env.local       # fill in OPENAI_API_KEY
npm install
npx vercel dev                   # http://localhost:3000
```

## Deploy

### Vercel (zero-config)
```bash
cd carousel-studio
npx vercel              # follow prompts
# Set env vars in the Vercel dashboard:
#   OPENAI_API_KEY=sk-...
#   GEMS_API_BASE=https://gems-poi-production.up.railway.app
npx vercel --prod
```

### Anywhere else
The `public/` folder is pure static — any host serves it. The `api/*.js` files expect a Vercel-style `(req, res)` signature; for Cloudflare Workers / Netlify / Deno Deploy, wrap them with the host's adapter (Hono works everywhere).

## API contract

All endpoints are stateless. Frontend keeps the cumulative payload in `sessionStorage` and passes it forward.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/topics` | `{ poi, theme?, n_topics? }` | `{ topics: [{ id, title, hook, angle, source_cluster_ids[] }] }` |
| POST | `/api/outline` | `{ poi, topic, n_slides? }` | `{ outline: { n_slides, slides: [...] } }` |
| POST | `/api/direction` | `{ poi, outline }` | `{ style_bible: { palette, type_pairing, layout_vocabulary[], anti_slop_rules[], negative_prompt } }` |
| POST | `/api/prompt-slide` | `{ slide, style_bible, slide_index, total_slides }` | `{ prompt, layout, size }` |
| POST | `/api/render-slide` | `{ prompt, size?, quality? }` | `{ image_b64, cost_usd, latency_ms }` |

## Cost per carousel (rough)
- Topics: $0.001 (gpt-4o-mini)
- Outline: $0.02 (gpt-4o)
- Direction: $0.02 (gpt-4o)
- 6× image prompts: $0.006 (gpt-4o-mini)
- 6× gpt-image-2 renders at `quality=medium`: $0.24
- **Total ~$0.30 per carousel.** High quality renders ~$1.10.

## Anti-AI-slop strategy
The Design Director can only choose from a curated palette/typeface/layout library defined in `lib/constants.js`. The Image Prompter writes prompts that pin the chosen palette hex values, typeface names, and editorial layout vocabulary into every `gpt-image-2` call, with a negative prompt that explicitly bans gradients, glassmorphism, generic stock-photo aesthetic, neon glow, and saturation boost.

Want a new look? Add it to `lib/constants.js`. Don't free-form prompt the LLM — the entire point is constrained choice.

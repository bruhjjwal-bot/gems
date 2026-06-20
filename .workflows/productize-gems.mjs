export const meta = {
  name: 'productize-gems',
  description: 'PM panel → ideate 100+ products on POI insight data, RICE-score, design+build top picks as polished hosted apps',
  phases: [
    { title: 'Recon', detail: 'snapshot data + current endpoints + design refs' },
    { title: 'Ideate', detail: '7 parallel PMs, one per user-journey lane, 15+ ideas each' },
    { title: 'Score', detail: 'RICE pass across the full ~100-idea pool' },
    { title: 'Feasibility', detail: 'tech-lead audit of top candidates against real API/data' },
    { title: 'Spec', detail: 'design + tech spec per chosen app, parallel' },
    { title: 'Build', detail: 'parallel implementation, each app to its own file' },
    { title: 'Wire', detail: 'API extensions + static serving + landing page + docs' },
  ],
}

const REPO = '/Users/headout/Documents/Gems/gems-scraper'

const CONTEXT = `
PROJECT: Gems POI Intelligence System (hackathon weekend sprint).

WHAT WE'VE BUILT:
- A 4-step L2 pipeline turning raw reviews/transcripts into labelled, cluster-level "insights".
- 1,450 clusters and 4,622 enriched insights across Louvre Museum + Colosseum.
- Sources: TripAdvisor reviews, Google reviews, Reddit posts/comments, YouTube transcripts.
- Each cluster has: name, l1/l2/l3 taxonomy, sentiment, anchor_entities, source_mix, quality_tier (A/B/C), size.
- Each insight has: cluster_id, l1/l2/l3, rating, intent_tag, sentiment, numeric_anchors, sub_attraction, raw quote text, source_id.
- Live API at https://gems-poi-production.up.railway.app with 6 endpoints:
  * POST /api/ask — full LLM RAG with citations (2-4s, OpenAI behind it)
  * POST /api/search_clusters — semantic cluster search (~200ms, no LLM)
  * POST /api/search_insights — per-row insight search w/ structured filters (~200ms)
  * POST /api/explain_cluster — citation trail (raw reviewer quotes) for a cluster_id
  * GET  /api/pois — POI metadata
  * GET  /api/health
- CORS open, deployable via 'railway up' in seconds.

HEADOUT CONTEXT:
- Headout sells travel experiences (tours, tickets, day trips).
- Users move through a journey: Explore → Plan → Book → Pre-visit → Visit → Post-visit.
- Our data covers ONLY Louvre + Colosseum for this exercise (hard scope).
- We don't have Headout's catalog, but we can pretend any "search Headout's catalog" call exists as a stub.
- Killer angle: our reviews are REAL human signal, not AI slop. Every claim can be cited back to a Redditor/reviewer/YouTuber.

TAXONOMY (intent_l1):
- Visit Intelligence (queue/timing/booking decisions)
- Attention Intelligence (what to see, hidden gems)
- Discovery Intelligence (sub-attractions, nearby)
- Culinary Intelligence (food)
- Operational Intelligence (warnings, scams, restrictions, access)

INTENT TAGS on insights: warning, recommendation, explanation, comparison, history, description.
SENTIMENT: positive, negative, neutral, mixed.

DESIGN PHILOSOPHY — explicitly NOT AI slop:
- AI slop = generic Tailwind purple/blue gradients, glassmorphism, Inter font everywhere, shadcn-card stacks, Lucide icons, "Powered by AI" badges, gradient hero sections.
- WE WANT = editorial typography (serif headlines + sans body + mono for data), constrained palettes (1 considered accent), real visual hierarchy, magazine/newspaper layouts where appropriate, real content density, drop caps + pull quotes + footnotes + marginalia where it fits.
- References: NYT cooking, FT.com, Pitchfork, Lapham's Quarterly, Atlas Obscura, Cabel.com, Are.na, Reading.am. Editorial publications, not generic SaaS landing pages.
- Use Google Fonts CDN (Inter is OK as ONE part of a stack but not the only voice). Good choices: Crimson Pro, Newsreader, Fraunces, EB Garamond (serif); IBM Plex / GT America / Inter (sans); JetBrains Mono / IBM Plex Mono (mono).

CONSTRAINTS:
- Louvre + Colosseum data only. If an idea needs "all POIs" it's out.
- Frontend = single HTML file per app, hosted at /<slug> on the existing Flask server. Vanilla JS or minimal framework via CDN. No build step.
- Each app must work against the live Railway URL.
- LLM cost is OK to spend but don't make every app a chatbot.
- 3-5 polished apps > 10 mediocre ones.
`

phase('Recon')
const recon = await agent(
  `Read these files and return a concise 200-word recon snapshot:
   1. ${REPO}/l2/api_server.py — current Flask endpoints, request/response shapes
   2. ${REPO}/DEPLOY.md — API reference for teammates
   3. ${REPO}/l2/mcp_server/config.py — taxonomy + ranking config
   4. ${REPO}/demo/index.html (peek at first 200 lines) — what the existing dashboard looks like
   5. Sample 5 entries from ${REPO}/l2/data/flat_insights_enriched.json to see what real insight rows look like (use Bash + python -c 'import json; print(json.dumps(json.load(open("${REPO}/l2/data/flat_insights_enriched.json"))[:5], indent=2))')
   6. Sample 5 entries from ${REPO}/l2/data/labelled_clusters_all.json

   Return a structured snapshot capturing: existing endpoints with shape, sample cluster shape, sample insight shape, what the existing dashboard already covers (so we don't duplicate).`,
  {
    label: 'recon',
    schema: {
      type: 'object',
      properties: {
        endpoints_summary: { type: 'string' },
        cluster_shape: { type: 'string' },
        insight_shape: { type: 'string' },
        existing_dashboard_covers: { type: 'string' },
        usable_data_features: { type: 'array', items: { type: 'string' } },
      },
      required: ['endpoints_summary', 'cluster_shape', 'insight_shape', 'usable_data_features']
    }
  }
)

log(`Recon done. ${recon.usable_data_features?.length || 0} data features available.`)

phase('Ideate')
const LANES = [
  { name: 'Pre-trip Exploration', jtbd: 'I want to decide WHERE/WHETHER to go — researching options' },
  { name: 'Itinerary Planning', jtbd: 'I have decided to go, now plan my visit / trip' },
  { name: 'Pre-visit Briefing', jtbd: 'I am 24h from my visit, what should I know NOW' },
  { name: 'In-visit Assistance', jtbd: 'I am AT the museum/site — quick lookups, navigation, decisions on the fly (mobile-first)' },
  { name: 'Post-visit & Sharing', jtbd: 'My visit is over — share, remember, plan what is next' },
  { name: 'Marketing & Top-of-Funnel', jtbd: 'Headout marketing wants content (Instagram carousels, blog seeds, ad copy, SEO pages) backed by real reviewer signal' },
  { name: 'Internal Headout Tools', jtbd: 'Headout CX, content ops, product teams want internal tooling — competitive intel, complaint mining, FAQ generation, etc.' },
]

const IDEA_SCHEMA = {
  type: 'object',
  properties: {
    ideas: {
      type: 'array',
      minItems: 15,
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'Punchy product name. Not generic.' },
          tagline: { type: 'string', description: 'One-liner, 8-12 words. Concrete.' },
          description: { type: 'string', description: '2-3 sentences. What does it DO?' },
          user_persona: { type: 'string', description: 'Who specifically uses this?' },
          journey_stage: { type: 'string' },
          jtbd: { type: 'string', description: 'Job-to-be-done, verbed.' },
          data_features_used: { type: 'array', items: { type: 'string' } },
          why_us: { type: 'string', description: 'Why our reviews-data wins vs a generic LLM app or a generic Google search?' },
          novelty: { type: 'number', description: '1-5: 5 = nobody else can build this' },
          headout_value: { type: 'string', description: 'How does this drive Headout business value (conversion, retention, content, support deflection)?' },
        },
        required: ['name', 'tagline', 'description', 'user_persona', 'journey_stage', 'jtbd', 'data_features_used', 'why_us', 'headout_value']
      }
    }
  },
  required: ['ideas']
}

const ideaBatches = await parallel(LANES.map(lane => () =>
  agent(
    `You are a senior product manager at Headout. ${CONTEXT}

    Your lane: ${lane.name}
    JTBD: ${lane.jtbd}

    Generate 15 product ideas SPECIFICALLY for this lane. Be greedy with creativity — wildcards welcome. Mix:
    - 5 obvious-but-strong ideas (the table stakes)
    - 5 second-order ideas that combine our data features in non-obvious ways
    - 5 wildcards / surprising angles (could be a B2B internal tool, a creative content tool, a "physical" use case like a printable zine, a voice-only app, an AR overlay companion, etc.)

    Each idea should be feasible against our data (Louvre + Colosseum only, the cluster + insight fields described). If an idea needs scaling to more POIs to be valuable, skip it.

    Don't repeat the existing dashboard. Don't just propose "chatbot for X" 15 times. Each idea should be distinct in concept, not just persona variations.

    Return 15 ideas.`,
    { label: `ideate:${lane.name.split(' ')[0].toLowerCase()}`, schema: IDEA_SCHEMA, phase: 'Ideate' }
  ).catch(() => ({ ideas: [] }))
))

const allIdeas = ideaBatches.filter(Boolean).flatMap(r => r.ideas || [])
log(`Generated ${allIdeas.length} ideas across ${LANES.length} lanes.`)

phase('Score')
const SCORE_SCHEMA = {
  type: 'object',
  properties: {
    ranked: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          rank: { type: 'number' },
          name: { type: 'string' },
          journey_stage: { type: 'string' },
          tagline: { type: 'string' },
          reach: { type: 'number', description: '1-10: how many distinct users does this touch per month? Be honest given hackathon scope.' },
          impact: { type: 'number', description: '0.25, 0.5, 1, 2, 3 — per-user impact magnitude' },
          confidence: { type: 'number', description: '0-1: how confident the model works given Louvre+Colosseum data only' },
          effort: { type: 'number', description: 'person-days to ship a polished v1 (NOT scale-ready, just demo-quality)' },
          rice: { type: 'number', description: 'reach * impact * confidence / effort' },
          rationale: { type: 'string', description: '2 sentences: why this score' },
          headout_value: { type: 'string' },
        },
        required: ['rank', 'name', 'reach', 'impact', 'confidence', 'effort', 'rice', 'rationale']
      }
    },
    top_picks_for_feasibility: {
      type: 'array',
      items: { type: 'string', description: 'Idea name' },
      minItems: 10,
      maxItems: 14,
      description: 'Top 10-14 RICE-ranked names to send to feasibility'
    },
    coverage_notes: {
      type: 'string',
      description: 'Comment on journey-stage coverage of the top picks — are we covering exploration, planning, in-visit, marketing across the picks?'
    }
  },
  required: ['ranked', 'top_picks_for_feasibility', 'coverage_notes']
}

const scored = await agent(
  `You are a senior PM at Headout reviewing ${allIdeas.length} product proposals. Score each via RICE.

  ${CONTEXT}

  RICE definition:
  - Reach (1-10): users touched per month — for a demo on 2 POIs, "10" is an unrealistic ceiling; calibrate honestly
  - Impact (0.25/0.5/1/2/3): per-user magnitude
  - Confidence (0-1): how confident the system actually works given just 2 POIs and current data quality
  - Effort (person-days): days to ship a polished demo-quality v1 (single-page HTML + a couple of API calls)

  RICE = R * I * C / E.

  Then pick the 10-14 highest RICE that ALSO together cover a diverse spread of journey stages (don't pick 5 from the same lane). Coverage matters — we want at least one idea each from: pre-trip, planning, pre-visit/in-visit, post-visit/marketing, internal tooling.

  IDEAS:
  ${JSON.stringify(allIdeas, null, 2)}`,
  { label: 'rice-score', schema: SCORE_SCHEMA, phase: 'Score' }
)

log(`Scored. Top picks: ${scored.top_picks_for_feasibility?.join(', ') || '?'}`)

phase('Feasibility')
const FEASIBILITY_SCHEMA = {
  type: 'object',
  properties: {
    chosen: {
      type: 'array',
      minItems: 4,
      maxItems: 6,
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          slug: { type: 'string', description: 'kebab-case, used in URL path and filename' },
          tagline: { type: 'string' },
          description: { type: 'string' },
          journey_stage: { type: 'string' },
          buildable_with_current_data: { type: 'boolean' },
          new_api_endpoints_needed: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                path: { type: 'string' },
                method: { type: 'string' },
                purpose: { type: 'string' },
                request_shape: { type: 'string' },
                response_shape: { type: 'string' }
              },
              required: ['path', 'method', 'purpose']
            }
          },
          existing_endpoints_used: { type: 'array', items: { type: 'string' } },
          estimated_build_hours: { type: 'number' },
          frontend_complexity: { enum: ['low', 'medium', 'high'] },
          design_hook: { type: 'string', description: 'One sentence: what makes this look NOT like AI slop' },
        },
        required: ['name', 'slug', 'tagline', 'description', 'journey_stage', 'buildable_with_current_data', 'estimated_build_hours', 'frontend_complexity', 'design_hook']
      }
    },
    rejected_from_top: {
      type: 'array',
      items: {
        type: 'object',
        properties: { name: { type: 'string' }, reason: { type: 'string' } },
        required: ['name', 'reason']
      }
    },
    consolidated_new_endpoints: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string' },
          method: { type: 'string' },
          purpose: { type: 'string' },
          request_shape: { type: 'string' },
          response_shape: { type: 'string' },
          implementation_notes: { type: 'string' },
        },
        required: ['path', 'method', 'purpose']
      },
      description: 'Deduplicated across all chosen apps — these are the new Flask endpoints we will actually add.'
    }
  },
  required: ['chosen', 'consolidated_new_endpoints']
}

const feasible = await agent(
  `You are a tech lead. ${CONTEXT}

  The PM has ranked these top picks:
  ${JSON.stringify(scored.ranked.filter(r => scored.top_picks_for_feasibility.includes(r.name)), null, 2)}

  Audit them and pick 4-6 to actually build today. Optimize for:
  1. Visual diversity (don't pick 5 chat UIs)
  2. Journey-stage diversity (cover at least 4 different lanes)
  3. Demo wow-factor (the team will see these — surprise + delight beats incremental)
  4. Feasibility against current API + data
  5. Total new-endpoint work that 1 dev can add to api_server.py in ~30 min

  RECON snapshot:
  ${JSON.stringify(recon, null, 2)}

  For each chosen app, specify any new endpoints needed. Consolidate across apps — if 2 apps need an itinerary endpoint, that's ONE endpoint.

  IMPORTANT:
  - Slug must be kebab-case and URL-safe.
  - For new endpoints, keep them small. Anything that needs >50 lines of Python should be either an LLM-call wrapper or rejected as too complex.
  - Each chosen app must be buildable as a SINGLE HTML FILE + the API. No multi-page apps.`,
  { label: 'feasibility', schema: FEASIBILITY_SCHEMA, phase: 'Feasibility' }
)

log(`Chosen apps (${feasible.chosen.length}): ${feasible.chosen.map(a => a.name).join(', ')}`)
log(`New endpoints to add (${feasible.consolidated_new_endpoints.length}): ${feasible.consolidated_new_endpoints.map(e => `${e.method} ${e.path}`).join(', ')}`)

phase('Spec')
const SPEC_SCHEMA = {
  type: 'object',
  properties: {
    name: { type: 'string' },
    slug: { type: 'string' },
    file_path: { type: 'string' },
    design_manifesto: { type: 'string', description: '3-4 sentences on the visual concept. Reference one of the editorial inspirations.' },
    typography: {
      type: 'object',
      properties: {
        headline_font: { type: 'string', description: 'Specific Google Font name + weight' },
        body_font: { type: 'string' },
        mono_font: { type: 'string' },
        rationale: { type: 'string' }
      },
      required: ['headline_font', 'body_font']
    },
    color_palette: {
      type: 'object',
      properties: {
        bg: { type: 'string' },
        fg: { type: 'string' },
        muted: { type: 'string' },
        accent: { type: 'string' },
        rationale: { type: 'string' }
      },
      required: ['bg', 'fg', 'accent']
    },
    layout_description: { type: 'string', description: '5-7 sentences describing the page layout, components, and interaction model. Should give the build agent enough to implement.' },
    api_calls: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          when: { type: 'string' },
          method: { type: 'string' },
          path: { type: 'string' },
          body_example: { type: 'string' },
          response_handling: { type: 'string' }
        }
      }
    },
    sample_content: { type: 'string', description: 'A few example data points the app should be able to render — concrete, e.g. sample queries, sample cluster names, sample editorial pull quotes' },
    interactions: { type: 'array', items: { type: 'string' } },
    states: { type: 'array', items: { type: 'string' }, description: 'Loading, empty, error, etc.' },
  },
  required: ['name', 'slug', 'file_path', 'design_manifesto', 'typography', 'color_palette', 'layout_description', 'api_calls']
}

const specs = await parallel(feasible.chosen.map(app => () =>
  agent(
    `You are a senior product designer at a top editorial publication (think NYT / FT / Atlas Obscura). You are scoping ONE app from the Gems Intelligence sprint.

    ${CONTEXT}

    APP TO SPEC:
    Name: ${app.name}
    Tagline: ${app.tagline}
    Description: ${app.description}
    Journey stage: ${app.journey_stage}
    Design hook: ${app.design_hook}
    Endpoints used: ${(app.existing_endpoints_used || []).join(', ')} + new: ${(app.new_api_endpoints_needed || []).map(e => `${e.method} ${e.path}`).join(', ')}

    AVAILABLE NEW ENDPOINTS BEING BUILT:
    ${JSON.stringify(feasible.consolidated_new_endpoints, null, 2)}

    Return a tight design + tech spec.

    DESIGN REQUIREMENTS:
    - File path: ${REPO}/public/${app.slug}.html
    - Single self-contained HTML (no build step).
    - Load fonts from Google Fonts CDN. Pick a specific editorial font pairing.
    - Constrained palette — pick ONE accent color (not gradient soup). Background can be off-white / paper / cream / dark — your choice.
    - Real visual hierarchy: headline ≫ deck ≫ body ≫ caption.
    - Avoid: gradient hero, glassmorphism, generic shadcn card stack, Inter as the only voice, "Powered by AI" badges, Lucide icon soup.
    - Reference at least one of: NYT cooking, FT.com, Pitchfork, Lapham's Quarterly, Atlas Obscura, Cabel.com, Are.na.
    - API base for production: https://gems-poi-production.up.railway.app (or '' for relative — the page is served by the same Flask host).
    - The build agent will implement what you spec — write the layout in enough detail that they don't have to make design decisions.
    - Include specific concrete sample content / sample queries / sample cluster names from the data so the page feels populated even with no user input.`,
    { label: `spec:${app.slug}`, schema: SPEC_SCHEMA, phase: 'Spec' }
  ).catch((e) => null)
))

const validSpecs = specs.filter(Boolean)
log(`Specs ready for ${validSpecs.length} apps.`)

phase('Build')

const BUILD_SCHEMA = {
  type: 'object',
  properties: {
    name: { type: 'string' },
    file_path: { type: 'string' },
    file_written: { type: 'boolean' },
    line_count: { type: 'number' },
    notes_for_user: { type: 'string', description: 'Anything the human should know — design choices, places to swap content, any data assumptions made.' },
  },
  required: ['name', 'file_path', 'file_written']
}

const builds = await parallel(validSpecs.map(spec => () =>
  agent(
    `You are a senior front-end engineer. Build the app described below as a SINGLE polished HTML file at ${spec.file_path}.

    SPEC:
    ${JSON.stringify(spec, null, 2)}

    PROJECT CONTEXT:
    ${CONTEXT}

    AVAILABLE BACKEND API at https://gems-poi-production.up.railway.app (also available at same origin '/'):
    - POST /api/ask: {query, poi, booking_context?} -> {answer, citations[], confidence, supporting_clusters[]}
    - POST /api/search_clusters: {poi, query, intent_l1?, limit?, min_tier?} -> ClusterCard[]
    - POST /api/search_insights: {poi, query, filters?, limit?} -> InsightCard[]
    - POST /api/explain_cluster: {cluster_id, max_quotes?} -> {cluster, quotes[]}
    - GET  /api/pois -> POI[]
    - GET  /api/health -> {status, clusters, insights, pois}

    Plus the NEW endpoints being added (will be live shortly):
    ${JSON.stringify(feasible.consolidated_new_endpoints, null, 2)}

    BUILD REQUIREMENTS:
    1. Write the file to: ${spec.file_path}
    2. Single self-contained HTML — inline CSS, inline JS, fonts from CDN. No build step.
    3. Make it WORK against the live API. Use:
         const API_BASE = location.origin.includes('localhost') ? 'http://localhost:8000' : '';
       (Empty string means same-origin — since Flask serves us.)
    4. Visual quality is everything — this is what the team will see. NO AI slop:
       - Editorial type pairing (serif + sans + mono as specced)
       - Constrained palette as specced
       - Real visual hierarchy
       - Use white-space and asymmetry intentionally
       - Smooth transitions but no glassmorphism / no purple gradients
       - Include specific concrete content from the data so the page feels alive at first load
    5. Handle: loading state, empty state, error state. Show citations / sources prominently.
    6. POI dropdown must default to Colosseum OR Louvre Museum (case-sensitive — those are the exact strings).
    7. Mobile-friendly basic styling.
    8. Add a small "← Gems" link top-left that goes to "/" (the landing page).

    Use the Write tool to write the file. Aim for 400-900 lines of HTML. Don't pad — every line should earn its place.

    Return: confirmation the file was written, line count, and any notes.`,
    { label: `build:${spec.slug}`, schema: BUILD_SCHEMA, phase: 'Build' }
  ).catch(e => ({ name: spec.name, file_path: spec.file_path, file_written: false, notes_for_user: String(e) }))
))

const successfulBuilds = builds.filter(b => b && b.file_written)
log(`Built ${successfulBuilds.length}/${validSpecs.length} apps`)

phase('Wire')

const wireResult = await agent(
  `You are a senior backend engineer. Wire up the Gems demo suite.

  CONTEXT:
  We have a Flask app at ${REPO}/l2/api_server.py that exposes /api/* routes and is deployed to Railway.
  We just built ${successfulBuilds.length} static HTML apps in ${REPO}/public/ that need to be served by the same Flask host.
  We also need to add ${feasible.consolidated_new_endpoints.length} new API endpoints.

  STEPS YOU MUST DO IN ORDER:

  1. Read ${REPO}/l2/api_server.py to understand current structure.

  2. Add these new endpoints to api_server.py (keep each implementation small — LLM wrappers OK, use the existing openai client pattern):
  ${JSON.stringify(feasible.consolidated_new_endpoints, null, 2)}

     IMPORTANT for any LLM-using endpoint:
     - Use the OpenAI client already imported (or import 'from openai import OpenAI'; client = OpenAI()).
     - Default to gpt-4o-mini for cost. Use gpt-4o only if quality demands.
     - Always ground LLM output in retrieval — call search_clusters / search_insights / etc internally first, pass the evidence into the LLM prompt, require structured output via response_format={"type": "json_object"} where possible, and ALWAYS include citations.
     - Wrap each endpoint in try/except, return 500 with {"error": str(e)} on failure.
     - Add CORS-friendly headers (Flask-CORS already covers /api/*).

  3. Add static-file serving so /<slug> serves ${REPO}/public/<slug>.html and / serves ${REPO}/public/index.html.
     Pattern (use Flask send_from_directory):
       from flask import send_from_directory
       PUBLIC_DIR = (Path(__file__).resolve().parent.parent / "public")
       @app.route('/')
       def home():
           return send_from_directory(PUBLIC_DIR, 'index.html')
       @app.route('/<path:filename>')
       def static_files(filename):
           # Don't shadow /api/*
           if filename.startswith('api/'):
               abort(404)
           # Try filename, then filename.html
           candidates = [filename, f"{filename}.html"]
           for c in candidates:
               full = PUBLIC_DIR / c
               if full.is_file():
                   return send_from_directory(PUBLIC_DIR, c)
           abort(404)

  4. Write ${REPO}/public/index.html — a landing page that links to all the apps we built. Must follow same editorial design philosophy: no AI slop. Apps to link:
  ${JSON.stringify(successfulBuilds.map((b, i) => ({
    name: feasible.chosen[i]?.name,
    slug: feasible.chosen[i]?.slug,
    tagline: feasible.chosen[i]?.tagline,
    journey_stage: feasible.chosen[i]?.journey_stage,
  })), null, 2)}
     Style the landing page like a publication's masthead / contents page. Real serif + sans typography. Number the apps. Include short editorial copy describing the project (we built this in a weekend on real reviewer signal, not generic AI fluff).

  5. Make sure .railwayignore does NOT block the new public/ folder. Read ${REPO}/.railwayignore and ensure public/ is not excluded. If anything in there would block public/*.html, fix it.

  6. Update ${REPO}/.railwayignore to not block public/, l2/api_server.py.

  7. Append to ${REPO}/DEPLOY.md a new section "Demo Apps" listing each app + its URL + what it does.

  Use Read/Edit/Write/Bash tools. Be careful and surgical with edits to api_server.py — preserve existing endpoints. Test your edits by syntax-checking with: python3 -c "import ast; ast.parse(open('${REPO}/l2/api_server.py').read())"

  Return what you did.`,
  {
    label: 'wire',
    phase: 'Wire',
    schema: {
      type: 'object',
      properties: {
        endpoints_added: { type: 'array', items: { type: 'string' } },
        static_serving_wired: { type: 'boolean' },
        landing_page_written: { type: 'boolean' },
        railwayignore_updated: { type: 'boolean' },
        syntax_check_passed: { type: 'boolean' },
        notes: { type: 'string' },
      },
      required: ['endpoints_added', 'static_serving_wired', 'landing_page_written', 'syntax_check_passed']
    }
  }
)

log(`Wire complete. Endpoints added: ${wireResult.endpoints_added?.length || 0}. Syntax OK: ${wireResult.syntax_check_passed}.`)

return {
  ideas_generated: allIdeas.length,
  apps_built: successfulBuilds.length,
  chosen_apps: feasible.chosen.map(a => ({ name: a.name, slug: a.slug, journey: a.journey_stage })),
  rejected_top_picks: feasible.rejected_from_top || [],
  new_endpoints: feasible.consolidated_new_endpoints.map(e => `${e.method} ${e.path}`),
  wire_notes: wireResult.notes,
  build_notes: builds.filter(Boolean).map(b => ({ name: b.name, notes: b.notes_for_user })),
  rice_ranking: scored.ranked.slice(0, 15),
  coverage_notes: scored.coverage_notes,
}

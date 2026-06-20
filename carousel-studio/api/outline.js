// POST /api/outline
// Agent 2 — Slide Architect. Turns a chosen tip-topic into a slide structure
// where EVERY slide is a TIP + the WHY backed by real reviewer quotes.
//
// Body: { poi, topic, n_slides?: number }
// Returns: { outline: { topic, n_slides, slides: [...] }, grounded }

import { jsonChat } from "../lib/openai.js";
import { explainCluster, searchInsights } from "../lib/gems.js";
import { MODELS, TRAVEL_IG_CONTEXT, SLIDE_KINDS } from "../lib/constants.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { poi, topic, n_slides = 6 } = req.body || {};
    if (!poi || !topic?.title) return res.status(400).json({ error: "poi + topic.title required" });
    if (n_slides < 4 || n_slides > 10) return res.status(400).json({ error: "n_slides must be 4..10" });

    // Pull cluster-level explanations for the topic's source clusters
    const cluster_ids = Array.isArray(topic.source_cluster_ids) ? topic.source_cluster_ids.slice(0, 8) : [];
    const evidencePromises = cluster_ids.map((id) => explainCluster({ cluster_id: id, max_quotes: 4 }));

    // Pull positive-recommendation insights filtered to the sub_attraction (if present),
    // so we get tip-shaped real quotes anchored to specific places.
    if (topic.sub_attraction) {
      evidencePromises.push(
        searchInsights({
          poi,
          query: topic.the_tip || topic.title || "tips",
          filters: {
            sub_attraction: topic.sub_attraction,
            intent_tag: ["recommendation"],
            insight_sentiment: ["positive", "mixed"],
          },
          limit: 8,
        })
      );
    }

    const fetches = await Promise.all(evidencePromises);
    const subInsightsRaw = topic.sub_attraction ? fetches.pop() : [];

    const evidence = [];
    for (const r of fetches) {
      if (!r?.cluster) continue;
      evidence.push({
        cluster_id: r.cluster.cluster_id,
        cluster_name: r.cluster.name,
        l1: r.cluster.l1,
        sentiment: r.cluster.sentiment,
        anchor_entities: r.cluster.anchor_entities,
        quotes: (r.quotes || []).map((q) => ({
          source: q.source,
          text: (q.raw_text || q.text || "").slice(0, 320),
          rating: q.rating ?? null,
          source_id: q.source_id || null,
        })),
      });
    }
    const sub_attraction_insights = Array.isArray(subInsightsRaw)
      ? subInsightsRaw.map((i) => ({
          cluster_id: i.cluster_id,
          raw_text: (i.raw_text || i.text || "").slice(0, 280),
          numeric_anchors: i.numeric_anchors,
          rating: i.rating,
          source: i.source,
          source_id: i.source_id,
        }))
      : [];

    const grounded = evidence.length > 0 || sub_attraction_insights.length > 0;

    const system = `You are an editor architecting an Instagram carousel about ${poi}. Every slide must be a TIP backed by a REASON traced to real visitor signal. This carousel is for travellers who already decided to visit — your job is to make their visit better, not talk them out of it.

${TRAVEL_IG_CONTEXT}

YOU WILL LAY OUT EXACTLY ${n_slides} SLIDES.

EVERY SLIDE — non-negotiable rules:
1. It carries ONE specific actionable insight tied to a NAMED entity (a sub_attraction, a specific entrance, a room, a time, a price, a route).
2. It has a REASON section explaining WHY this tip holds — pull from the evidence quotes, cluster sentiment, ratings, or numeric anchors.
3. POSITIVE in framing. If the underlying pattern is a warning, REFRAME as the workaround/tip. ("Why Porte des Lions beats the Pyramid entrance" instead of "Avoid the Pyramid entrance").
4. Real numbers are gold. If a quote contains a number ("waited 5 minutes", "spent ~2 hours", "saved €15"), use it as the slide's key_fact.
5. Citation is mandatory — citation_cluster_id, citation_quote (real ≤120-char excerpt from a quote), citation_source.

SLIDE KIND ROLES (use the right kind for each slot):
${SLIDE_KINDS.map((k) => `- ${k.id}: ${k.role}`).join("\n")}

OPTIMAL STRUCTURE for ${n_slides} slides:
- Slide 1 (cover): commit to the tip in the headline itself — never tease. "Visit the Mona Lisa at 6 PM" not "There's a secret time to see the Mona Lisa".
- Slides 2 to n-2: a mix of tip + data + pull_quote. Each carries a single tip + its reason. Numeric anchors → data slides. Strong reviewer voices → pull_quote slides (positive quotes only).
- Slide n-1: a tip slide that nails the most useful, specific takeaway.
- Slide n (outro): a memorable, concrete closer — name the sub_attraction or named entity from the topic, leave them with the action they should take.

LENGTH BUDGETS (these are screen-tight, not arbitrary):
- headline: ≤9 words
- subhead: ≤14 words
- body_copy: ≤28 words
- key_fact: just the number + tight unit ("€22", "37 min", "8:30 AM")
- citation_quote: ≤120 chars

FORBIDDEN inside the slides:
- "Skip", "avoid", "regret", "overrated", "disappointing" — anywhere on any slide
- Pulling a negative reviewer quote into pull_quote — only positive or mixed-positive quotes
- Generic platitudes ("plan ahead", "do your research", "be prepared")
- Emoji`;

    const user = `POI: ${poi}
Topic: ${topic.title}
Hook: ${topic.hook}
The tip: ${topic.the_tip}
The reason: ${topic.the_reason}
Sub-attraction: ${topic.sub_attraction || "(not focused on one)"}
Numeric hook: ${topic.numeric_hook || "(none)"}
Want: ${n_slides} slides.

${grounded ? `EVIDENCE — cluster quotes (use these for citations):
${JSON.stringify(evidence, null, 2)}

EVIDENCE — sub-attraction insights with numeric anchors:
${JSON.stringify(sub_attraction_insights, null, 2)}` : "EVIDENCE: (unavailable — base slides on the topic only; do not invent cluster_ids or numbers)"}

Return JSON. Every slide is a TIP + a REASON.`;

    const schema = {
      type: "object",
      additionalProperties: false,
      properties: {
        outline: {
          type: "object",
          additionalProperties: false,
          properties: {
            topic_title: { type: "string" },
            n_slides: { type: "integer" },
            slides: {
              type: "array",
              minItems: n_slides,
              maxItems: n_slides,
              items: {
                type: "object",
                additionalProperties: false,
                properties: {
                  index: { type: "integer", description: "0-indexed position" },
                  kind: { enum: SLIDE_KINDS.map((k) => k.id) },
                  role_note: { type: "string", description: "1 sentence — what this slide does for the carousel" },
                  headline: { type: "string", description: "≤9 words. The TIP in plain words. Imperative or declarative — never a question." },
                  subhead: { type: "string", description: "≤14 words. Optional deck or supporting detail. May be empty." },
                  body_copy: { type: "string", description: "≤28 words. The REASON or the actionable detail. May be empty for cover/pull_quote/data." },
                  key_fact: { type: "string", description: "For data slides, the exact number + tight unit ('€22', '37 min'). Empty otherwise." },
                  named_entity: { type: "string", description: "The specific sub_attraction / room / entrance / route this slide centers on. e.g. 'Porte des Lions', 'Denon Wing', 'Roma Pass'. Required — even cover/outro slides must name something." },
                  citation_cluster_id: { type: ["integer", "null"] },
                  citation_quote: { type: "string", description: "≤120 chars, REAL excerpt from a POSITIVE quote in the evidence. Empty if none fits." },
                  citation_source: { type: "string", description: "platform — tripadvisor_review / reddit_post / etc. Empty if no citation." },
                },
                required: ["index", "kind", "role_note", "headline", "subhead", "body_copy", "key_fact", "named_entity", "citation_cluster_id", "citation_quote", "citation_source"],
              },
            },
          },
          required: ["topic_title", "n_slides", "slides"],
        },
      },
      required: ["outline"],
    };

    const out = await jsonChat({
      model: MODELS.text_heavy,
      system,
      user,
      schema,
      schemaName: "tip_carousel_outline",
      temperature: 0.65,
    });

    return res.status(200).json({ ...out, grounded });
  } catch (e) {
    console.error("outline error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

// POST /api/outline
// Agent 2 — Slide Architect. Turns a chosen topic into a slide-by-slide carousel structure
// with real reviewer quotes pulled from explain_cluster.
//
// Body: { poi, topic: {title,hook,angle,source_cluster_ids[]}, n_slides?: number }
// Returns: { outline: { topic, n_slides, slides: [...] }, grounded }

import { jsonChat } from "../lib/openai.js";
import { explainCluster } from "../lib/gems.js";
import { MODELS, TRAVEL_IG_CONTEXT, SLIDE_KINDS } from "../lib/constants.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { poi, topic, n_slides = 6 } = req.body || {};
    if (!poi || !topic?.title) return res.status(400).json({ error: "poi + topic.title required" });
    if (n_slides < 4 || n_slides > 10) return res.status(400).json({ error: "n_slides must be 4..10" });

    // Pull evidence quotes for each source cluster
    const cluster_ids = Array.isArray(topic.source_cluster_ids) ? topic.source_cluster_ids.slice(0, 8) : [];
    const evidence = [];
    if (cluster_ids.length) {
      const fetches = await Promise.all(cluster_ids.map((id) => explainCluster({ cluster_id: id, max_quotes: 3 })));
      for (const r of fetches) {
        if (!r?.cluster) continue;
        evidence.push({
          cluster_id: r.cluster.cluster_id,
          cluster_name: r.cluster.name,
          l1: r.cluster.l1,
          sentiment: r.cluster.sentiment,
          quotes: (r.quotes || []).map((q) => ({
            source: q.source,
            text: (q.raw_text || q.text || "").slice(0, 320),
            rating: q.rating ?? null,
            source_id: q.source_id || null,
          })),
        });
      }
    }
    const grounded = evidence.length > 0;

    const system = `You are a senior magazine editor architecting an Instagram carousel about ${poi}.

${TRAVEL_IG_CONTEXT}

You will lay out exactly ${n_slides} slides. Every slide does ONE job and only one. Choose slide.kind from:
${SLIDE_KINDS.map((k) => `- ${k.id}: ${k.role}`).join("\n")}

Hard rules:
- Slide 1 MUST be a cover that creates a curiosity gap or stakes a contrarian claim — never a generic title card.
- At least one slide MUST be a pull_quote drawing from real reviewer quotes in the evidence.
- At least one slide should carry a specific number or fact (data kind) if the evidence supports one.
- Final slide is either an outro (memorable closer) or a soft CTA, not a hard sell.
- Body copy on each slide is short enough to fit on a 1080×1080 image at editorial type sizes — keep "body_copy" ≤ 28 words, "headline" ≤ 9 words, "subhead" ≤ 14 words.
- Every slide MUST cite a cluster_id from the evidence (citation_cluster_id). citation_quote should be a real ≤120-char excerpt from that cluster's quotes when relevant; otherwise paraphrase faithfully.
- No emoji anywhere.`;

    const user = `POI: ${poi}
Topic: ${topic.title}
Hook: ${topic.hook}
Angle: ${topic.angle}
Want: ${n_slides} slides.

${grounded ? `EVIDENCE (cluster names + real reviewer quotes):\n${JSON.stringify(evidence, null, 2)}` : "EVIDENCE: (unavailable — base slides on the topic angle alone; do not invent cluster_ids)"}

Return JSON.`;

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
                  headline: { type: "string", description: "≤9 words, the dominant type on the slide" },
                  subhead: { type: "string", description: "≤14 words, deck or callout, may be empty" },
                  body_copy: { type: "string", description: "≤28 words, may be empty for cover/pull_quote/data" },
                  key_fact: { type: "string", description: "if data kind, the exact number + unit; else empty" },
                  citation_cluster_id: { type: ["integer", "null"] },
                  citation_quote: { type: "string", description: "≤120 chars, a real or faithful excerpt; empty if no evidence available" },
                  citation_source: { type: "string", description: "platform — tripadvisor_review / reddit_post / etc; empty if none" },
                },
                required: ["index", "kind", "role_note", "headline", "subhead", "body_copy", "key_fact", "citation_cluster_id", "citation_quote", "citation_source"],
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
      schemaName: "carousel_outline",
      temperature: 0.7,
    });

    return res.status(200).json({ ...out, grounded });
  } catch (e) {
    console.error("outline error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

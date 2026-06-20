// POST /api/topics
// Agent 1 — Topic Scout. Generates carousel topic ideas grounded in Gems cluster data.
//
// Body: { poi: "Louvre Museum" | "Colosseum", theme?: string, n_topics?: number }
// Returns: { topics: [{ id, title, hook, angle, source_cluster_ids[], why_it_works }], grounded: boolean }

import { jsonChat } from "../lib/openai.js";
import { gatherEvidence } from "../lib/gems.js";
import { MODELS, TRAVEL_IG_CONTEXT } from "../lib/constants.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { poi, theme = "", n_topics = 8 } = req.body || {};
    if (!poi) return res.status(400).json({ error: "poi required" });
    if (n_topics < 3 || n_topics > 12) return res.status(400).json({ error: "n_topics must be 3..12" });

    const evidence = await gatherEvidence(poi, theme);
    const grounded = Array.isArray(evidence) && evidence.length > 0;

    const system = `You are a senior content lead at a travel publication (Atlas Obscura / Boat magazine / Suitcase).
Your job is to invent ${n_topics} Instagram carousel TOPIC IDEAS that earn attention.

${TRAVEL_IG_CONTEXT}

Constraints on every topic:
- Must be specific to the requested POI, never generic ("things to do" is banned).
- Must hint at a curiosity gap, a contrarian take, a hidden mechanism, a surprising number, a real-person story, or a counter-intuitive tip.
- Must be a topic a real publication would publish — not LLM-style listicle slop.
- If the user gave a theme, every topic should respect it; if not, span a diverse mix.

For each topic, ground it in 3-5 real cluster IDs from the evidence below. The cluster names ARE the human signal — quote-mine them.`;

    const evidenceBlock = grounded
      ? `EVIDENCE (real clusters from ${poi}, with sentiment + size):\n${JSON.stringify(evidence.slice(0, 40), null, 2)}`
      : `EVIDENCE: (Gems API unreachable — invent plausible but specific topics for ${poi}. Do not hallucinate cluster_ids; leave source_cluster_ids empty.)`;

    const user = `POI: ${poi}
${theme ? `Theme: ${theme}` : "Theme: open — span topic types"}
Want: ${n_topics} topic ideas.

${evidenceBlock}

Return JSON.`;

    const schema = {
      type: "object",
      additionalProperties: false,
      properties: {
        topics: {
          type: "array",
          minItems: n_topics,
          maxItems: n_topics,
          items: {
            type: "object",
            additionalProperties: false,
            properties: {
              id: { type: "string", description: "kebab-case slug, unique" },
              title: { type: "string", description: "Editorial title, 4-9 words" },
              hook: { type: "string", description: "One line you could open the carousel with — creates the curiosity gap" },
              angle: { type: "string", description: "What's the underlying argument or surprise of this topic in one sentence" },
              source_cluster_ids: {
                type: "array",
                items: { type: "integer" },
                description: "Cluster IDs from evidence that this topic draws on; 3-5 ideal",
              },
              why_it_works: { type: "string", description: "1 sentence — why a stranger scrolling Instagram stops on slide 1" },
            },
            required: ["id", "title", "hook", "angle", "source_cluster_ids", "why_it_works"],
          },
        },
      },
      required: ["topics"],
    };

    const out = await jsonChat({
      model: MODELS.text_light,
      system,
      user,
      schema,
      schemaName: "topic_ideas",
      temperature: 0.85,
    });

    return res.status(200).json({ ...out, grounded, n_clusters_seen: grounded ? evidence.length : 0 });
  } catch (e) {
    console.error("topics error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

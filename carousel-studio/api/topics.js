// POST /api/topics
// Agent 1 — Topic Scout. Tip-centric topic ideation grounded in real positive
// reviewer signal: named sub-attractions, numeric anchors from recommendations,
// and tier-A clusters. Never proposes "skip X" or negative framings.
//
// Body: { poi: "Louvre Museum" | "Colosseum", theme?: string, n_topics?: number }
// Returns: { topics: [{ id, title, hook, the_tip, the_reason, source_cluster_ids[], sub_attraction?, numeric_hook? }], grounded: boolean }

import { jsonChat } from "../lib/openai.js";
import { gatherTipsEvidence } from "../lib/gems.js";
import { MODELS, TRAVEL_IG_CONTEXT } from "../lib/constants.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { poi, theme = "", n_topics = 8 } = req.body || {};
    if (!poi) return res.status(400).json({ error: "poi required" });
    if (n_topics < 3 || n_topics > 12) return res.status(400).json({ error: "n_topics must be 3..12" });

    const evidence = await gatherTipsEvidence(poi, theme);
    const grounded =
      (evidence.sub_attractions?.length || 0) +
      (evidence.tier_a_clusters?.length || 0) +
      (evidence.numeric_recommendations?.length || 0) > 0;

    const system = `You are a senior editor at a travel publication that has built a reputation on ONE thing: actionable insider tips backed by real visitor signal. Atlas Obscura crossed with Wirecutter for travel. Your readers are travellers about to visit ${poi} who want a sharp tip they can use, not vibes.

${TRAVEL_IG_CONTEXT}

THE BAR FOR EVERY TOPIC:
1. It is a SPECIFIC, ACTIONABLE TIP — "do X" or "visit Y at Z" or "the best W is at Q". Never "5 things you'll regret", never "is X overrated", never "skip Y".
2. It references a NAMED entity from the evidence — a sub_attraction (Mona Lisa, Denon Wing, Roma Pass, Porte des Lions, hypogeum, Arch of Constantine), a specific room/entrance, a specific time, a specific number.
3. It has a CONCRETE REASON backed by the evidence — a recommendation_count, a positive_count, a numeric anchor ("save 45 minutes", "avg 37 min spent"), or a tier-A cluster's signal weight.
4. It is POSITIVE in spirit. Even when the underlying lesson is "the queue is brutal", the topic frames the FIX, not the pain ("Why locals enter via Porte des Lions" — not "Avoid the Pyramid entrance").
5. It is SPECIFIC ENOUGH to be screenshotted and remembered. Wirecutter's headlines work because they commit to a recommendation; do the same.

FORBIDDEN PATTERNS — instant rejection if any topic uses these:
- "Skip", "avoid", "regret", "overrated", "disappointment", "not worth", "tourist trap" — anywhere in the title or hook
- "Did you know" / "5 things" / "Things tourists don't know" — patronising listicle phrasing
- Any contrarian "is X really worth it?" framing — we are not in the business of telling people not to visit
- Generic claims with no named entity, no number, no specific time, no specific place
- Topics whose only payoff is a warning. Warnings can appear later as one slide INSIDE a tip-framed topic, never as the topic itself

THE TITLE FORMULA:
Use one of these patterns (or vary, but stay in this energy):
- "Why [specific entity] is worth [specific time/effort]"
- "The best time to see [specific entity] (and the data behind it)"
- "How [N] visitors say to [specific action] at [specific place]"
- "The [specific entity] you should not miss at [POI]"
- "[Specific entity]: arrive by [time] and you'll see [outcome]"
- "[N] minutes at [sub_attraction] — here's what to actually do"

THE EVIDENCE LADDER for "the_reason":
- Strongest: a numeric anchor from the recommendations evidence (e.g. "avg visitor spends 37 min here", "saves ~45 min")
- Next: corroborated recommendation_count across N sources for a sub_attraction
- Next: tier-A cluster size + positive sentiment
Never invent a number. If a number appears in the_reason it must trace back to the evidence.`;

    const evidenceBlock = grounded
      ? `EVIDENCE for ${poi} (use this — every topic must cite from here):

SUB-ATTRACTIONS (named, with usage data):
${JSON.stringify(evidence.sub_attractions || [], null, 2)}

TIER-A CLUSTERS (high-confidence positive signal):
${JSON.stringify(evidence.tier_a_clusters || [], null, 2)}

NUMERIC RECOMMENDATIONS (real visitor tips with extracted numbers):
${JSON.stringify(evidence.numeric_recommendations || [], null, 2)}`
      : `EVIDENCE: (Gems API unreachable for ${poi}. Skip this — return an empty topics array.)`;

    if (!grounded) {
      return res.status(503).json({ error: "Gems evidence API unreachable; refusing to ideate ungrounded.", topics: [] });
    }

    const user = `POI: ${poi}
${theme ? `Theme requested: ${theme} (interpret this as a TIP angle within the bar above — never as a negative angle)` : "Theme: open — pick the strongest tips signal across the evidence"}
Want: ${n_topics} topic ideas.

${evidenceBlock}

Return JSON. Every topic must satisfy every rule above. If you cannot find enough specific signal for ${n_topics} topics, return fewer.`;

    const schema = {
      type: "object",
      additionalProperties: false,
      properties: {
        topics: {
          type: "array",
          minItems: 3,
          maxItems: n_topics,
          items: {
            type: "object",
            additionalProperties: false,
            properties: {
              id: { type: "string", description: "kebab-case slug, unique" },
              title: { type: "string", description: "Editorial title, 5-10 words. Must follow the title formula — POSITIVE, SPECIFIC, NAMED entity." },
              hook: { type: "string", description: "One line opener for slide 1. Commits to the tip, doesn't tease." },
              the_tip: { type: "string", description: "ONE sentence: the actionable tip a reader walks away with. Imperative voice — 'visit X at Y', 'enter via Z', 'spend N min on W'." },
              the_reason: { type: "string", description: "ONE sentence: WHY this tip holds, traced to a number, count, or pattern from the evidence. e.g. 'recommended by 28 reviewers across Reddit and TripAdvisor with an average 37 min spent here.'" },
              sub_attraction: { type: ["string", "null"], description: "Named sub-attraction from evidence if the topic centers on one; else null" },
              numeric_hook: { type: ["string", "null"], description: "The specific number this topic leverages — '37 min', '$22', '8:30am'. null if no specific number." },
              source_cluster_ids: {
                type: "array",
                items: { type: "integer" },
                description: "Cluster IDs from evidence that back this topic; 2-5 ideal. Must be real IDs from the evidence above.",
              },
              why_it_works: { type: "string", description: "1 sentence — why a stranger scrolling Instagram stops on slide 1. Frame the value, not the curiosity gap." },
            },
            required: ["id", "title", "hook", "the_tip", "the_reason", "sub_attraction", "numeric_hook", "source_cluster_ids", "why_it_works"],
          },
        },
      },
      required: ["topics"],
    };

    const out = await jsonChat({
      model: MODELS.text_heavy, // gpt-4o — quality matters here for the punch
      system,
      user,
      schema,
      schemaName: "tip_topics",
      temperature: 0.75,
    });

    return res.status(200).json({
      ...out,
      grounded,
      evidence_summary: {
        sub_attractions: evidence.sub_attractions?.length || 0,
        tier_a_clusters: evidence.tier_a_clusters?.length || 0,
        numeric_recommendations: evidence.numeric_recommendations?.length || 0,
      },
    });
  } catch (e) {
    console.error("topics error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

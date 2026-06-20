// POST /api/direction
// Agent 3 — Design Director. Given an outline, picks a palette + type pairing + 2-3 layouts
// from the CURATED constants library (cannot freelance). Writes anti-slop rules and a negative prompt.
//
// Body: { poi, outline }
// Returns: { style_bible: {...} }

import { jsonChat } from "../lib/openai.js";
import { MODELS, PALETTES, TYPE_PAIRINGS, LAYOUTS, ANTI_SLOP_RULES, TRAVEL_IG_CONTEXT } from "../lib/constants.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { poi, outline } = req.body || {};
    if (!poi || !outline?.slides) return res.status(400).json({ error: "poi + outline.slides required" });

    const system = `You are a design director at a travel magazine choosing a visual language for a single Instagram carousel about ${poi}.

You CANNOT invent palettes, typefaces, or layouts. You MUST choose from the curated libraries provided. Free-form choices are how AI slop happens — the entire point of this system is constrained editorial choice.

${TRAVEL_IG_CONTEXT}

Pick exactly:
- ONE palette by id from the palette library
- ONE type_pairing by id from the type library
- TWO or THREE layout ids from the layout library — these are the only layouts this carousel will use across all slides

Then write the carousel's anti-slop manifest: 5 rules tailored to this topic, plus a negative_prompt string that will be appended to every gpt-image-2 call to ban AI-slop visual patterns.

Match palette mood and type voice to the outline's tone: contrarian sharp = newsprint + GT Sectra-adjacent; reverent + historical = Roman Stone + Fraunces; sunlit + earthen = Vatican Plaster + Garamond; declarative + numeric = Cobalt Press + Plex; etc.`;

    const palette_ids = PALETTES.map((p) => p.id);
    const type_ids = TYPE_PAIRINGS.map((t) => t.id);
    const layout_ids = LAYOUTS.map((l) => l.id);

    const user = `POI: ${poi}
Outline:
${JSON.stringify({ topic: outline.topic_title, slides: outline.slides.map((s) => ({ kind: s.kind, headline: s.headline })) }, null, 2)}

PALETTE LIBRARY:
${JSON.stringify(PALETTES, null, 2)}

TYPE PAIRING LIBRARY:
${JSON.stringify(TYPE_PAIRINGS, null, 2)}

LAYOUT LIBRARY:
${JSON.stringify(LAYOUTS, null, 2)}

UNIVERSAL ANTI-SLOP RULES (always apply, you may add tailored ones):
${ANTI_SLOP_RULES.map((r) => `- ${r}`).join("\n")}

Return JSON. palette_id, type_pairing_id, and every entry in layout_ids MUST be a valid id from the libraries above.`;

    const schema = {
      type: "object",
      additionalProperties: false,
      properties: {
        style_bible: {
          type: "object",
          additionalProperties: false,
          properties: {
            palette_id: { enum: palette_ids },
            type_pairing_id: { enum: type_ids },
            layout_ids: {
              type: "array",
              minItems: 2,
              maxItems: 3,
              items: { enum: layout_ids },
            },
            photo_treatment: { type: "string", description: "1 sentence describing photography or illustration treatment (eg 'film grain documentary' or 'engraved-line illustration')" },
            tonal_direction: { type: "string", description: "1 sentence — overall mood (reverent / contrarian / wry / clinical / etc)" },
            anti_slop_rules: {
              type: "array",
              minItems: 5,
              maxItems: 7,
              items: { type: "string" },
            },
            negative_prompt: { type: "string", description: "One comma-separated string appended to every image prompt — bans the visual cliches this carousel must avoid" },
            rationale: { type: "string", description: "2 sentences — why these choices fit this topic" },
          },
          required: ["palette_id", "type_pairing_id", "layout_ids", "photo_treatment", "tonal_direction", "anti_slop_rules", "negative_prompt", "rationale"],
        },
      },
      required: ["style_bible"],
    };

    const out = await jsonChat({
      model: MODELS.text_heavy,
      system,
      user,
      schema,
      schemaName: "style_bible",
      temperature: 0.6,
    });

    // Hydrate the bible with the full constant objects so the client doesn't need the library.
    const sb = out.style_bible;
    const hydrated = {
      ...sb,
      palette: PALETTES.find((p) => p.id === sb.palette_id),
      type_pairing: TYPE_PAIRINGS.find((t) => t.id === sb.type_pairing_id),
      layouts: sb.layout_ids.map((id) => LAYOUTS.find((l) => l.id === id)).filter(Boolean),
    };

    return res.status(200).json({ style_bible: hydrated });
  } catch (e) {
    console.error("direction error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

// POST /api/prompt-slide
// Agent 4 — Image Prompter. Writes the gpt-image-2 prompt for ONE slide using the style bible.
// Picks the best layout from the bible's vocabulary for this slide's kind.
//
// Body: { slide, style_bible, slide_index, total_slides }
// Returns: { prompt, layout_id, size, suggested_quality }

import { jsonChat } from "../lib/openai.js";
import { MODELS, LAYOUTS } from "../lib/constants.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { slide, style_bible, slide_index, total_slides } = req.body || {};
    if (!slide || !style_bible) return res.status(400).json({ error: "slide + style_bible required" });

    const allowed_layout_ids = (style_bible.layout_ids || style_bible.layouts?.map((l) => l.id) || []).filter(Boolean);
    if (!allowed_layout_ids.length) return res.status(400).json({ error: "style_bible has no layout_ids" });

    // Re-hydrate layout objects so the prompter has descriptions
    const allowedLayouts = allowed_layout_ids.map((id) => LAYOUTS.find((l) => l.id === id)).filter(Boolean);

    const palette = style_bible.palette || {};
    const type_pairing = style_bible.type_pairing || {};

    const system = `You write image-generation prompts for gpt-image-2 — the latest OpenAI model that can render typography correctly inside images.

Your job: pick the most fitting layout (from this carousel's chosen layout vocabulary) for the given slide, then write ONE high-fidelity prompt that will produce a 1080×1080 Instagram carousel slide.

CRITICAL prompt-engineering rules for gpt-image-2:
- Always pin exact hex colors for the palette ("background #F4EFE6, ink #1A1614, oxblood accent #7A1F1F used only on …").
- Always name the exact typeface and weight ("headline rendered in Fraunces Bold opsz 144").
- Quote the EXACT text the model must render, in double quotes, with line breaks shown as " / ".
- Describe composition in spatial terms (rule of thirds, top-third, bottom-left, etc).
- Say what the SUBJECT of the image is — for non-photographic slides (pull_quote, data_number) the subject is the typographic composition itself.
- Append the negative_prompt verbatim at the end.
- DO NOT use the words "Instagram", "social media", "carousel" in the prompt — they bias toward Canva-style output.
- DO NOT include emoji, decorative borders, or the words "modern", "minimalist", "clean", "elegant" — they bias toward AI slop.`;

    const user = `Slide ${slide_index + 1} of ${total_slides}.
Kind: ${slide.kind}
Headline (must be rendered in the image): "${slide.headline}"
Subhead: "${slide.subhead || ''}"
Body copy: "${slide.body_copy || ''}"
Key fact: "${slide.key_fact || ''}"
Citation: ${slide.citation_quote ? `"${slide.citation_quote}" — ${slide.citation_source || ''}` : '(none)'}

STYLE BIBLE:
${JSON.stringify({
  palette,
  type_pairing,
  photo_treatment: style_bible.photo_treatment,
  tonal_direction: style_bible.tonal_direction,
  layout_vocabulary: allowedLayouts,
  anti_slop_rules: style_bible.anti_slop_rules,
  negative_prompt: style_bible.negative_prompt,
}, null, 2)}

You MUST pick layout_id from this carousel's allowed layouts: ${allowed_layout_ids.join(', ')}. Prefer layouts whose "best_for" includes this slide's kind, but cover slides with no perfect match can use any.

Return JSON.`;

    const schema = {
      type: "object",
      additionalProperties: false,
      properties: {
        layout_id: { enum: allowed_layout_ids },
        prompt: { type: "string", description: "The full gpt-image-2 prompt. 80-220 words. Pins hex codes, font names, and exact text to render." },
        rationale: { type: "string", description: "1 sentence — why this layout fits this slide" },
      },
      required: ["layout_id", "prompt", "rationale"],
    };

    const out = await jsonChat({
      model: MODELS.text_light,
      system,
      user,
      schema,
      schemaName: "image_prompt",
      temperature: 0.6,
    });

    return res.status(200).json({
      ...out,
      size: "1024x1024",
      suggested_quality: "medium",
    });
  } catch (e) {
    console.error("prompt-slide error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

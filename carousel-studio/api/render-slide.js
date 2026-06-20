// POST /api/render-slide
// Agent 5 — Renderer. Calls gpt-image-2 for one slide.
//
// Body: { prompt: string, size?: "1024x1024", quality?: "low"|"medium"|"high" }
// Returns: { image_b64, cost_usd, latency_ms, model }

import { generateImage } from "../lib/openai.js";
import { handleOptions } from "../lib/cors.js";

export default async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    const { prompt, size = "1024x1024", quality = "medium" } = req.body || {};
    if (!prompt || typeof prompt !== "string") return res.status(400).json({ error: "prompt required" });
    if (prompt.length > 4000) return res.status(400).json({ error: "prompt too long (max 4000 chars)" });
    if (!["low", "medium", "high"].includes(quality)) return res.status(400).json({ error: "quality must be low/medium/high" });
    if (!["1024x1024", "1024x1536", "1536x1024"].includes(size)) return res.status(400).json({ error: "invalid size" });

    const result = await generateImage({ prompt, size, quality });
    return res.status(200).json({
      image_b64: result.b64,
      cost_usd: result.cost_usd,
      latency_ms: result.latency_ms,
      model: result.model,
    });
  } catch (e) {
    console.error("render-slide error", e);
    return res.status(500).json({ error: String(e?.message || e) });
  }
}

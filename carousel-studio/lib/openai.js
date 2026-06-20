import OpenAI from "openai";

let _client = null;
export function client() {
  if (!_client) {
    if (!process.env.OPENAI_API_KEY) {
      throw new Error("OPENAI_API_KEY is not set");
    }
    _client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
  }
  return _client;
}

// Structured JSON chat completion. Returns the parsed object.
// schema: a JSON Schema object passed as response_format.json_schema.schema
export async function jsonChat({ model, system, user, schema, schemaName = "output", temperature = 0.7 }) {
  const c = client();
  const r = await c.chat.completions.create({
    model,
    temperature,
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
    response_format: {
      type: "json_schema",
      json_schema: { name: schemaName, schema, strict: true },
    },
  });
  const raw = r.choices?.[0]?.message?.content;
  if (!raw) throw new Error("Empty completion");
  return JSON.parse(raw);
}

// Image generation. Returns { b64, cost_usd, latency_ms, model }.
// Pricing is approximate — gpt-image-2 actual cost is on usage report.
export async function generateImage({ prompt, size = "1024x1024", quality = "medium", model }) {
  const c = client();
  const m = model || process.env.IMAGE_MODEL || "gpt-image-2";
  const t0 = Date.now();
  const r = await c.images.generate({
    model: m,
    prompt,
    n: 1,
    size,
    quality,
  });
  const latency_ms = Date.now() - t0;
  const datum = r.data?.[0];
  if (!datum) throw new Error("Image API returned no data");
  // gpt-image-* returns b64_json by default
  const b64 = datum.b64_json;
  if (!b64) {
    // Some accounts may get url response; fail loud so we know.
    throw new Error("Expected b64_json from image API; got url-only response");
  }
  const cost_usd = approxImageCost(quality, size);
  return { b64, cost_usd, latency_ms, model: m };
}

function approxImageCost(quality, size) {
  // Rough sticker prices for budgeting; actual billed cost comes via usage.
  const t = { low: 0.011, medium: 0.042, high: 0.167 };
  return t[quality] ?? t.medium;
}

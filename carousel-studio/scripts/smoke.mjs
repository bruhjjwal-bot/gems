// End-to-end smoke test against real OpenAI + real Gems API.
// Runs each handler directly with mock req/res. Uses low-quality render to save $$.
// Saves the rendered image to scripts/smoke-output/<slide-idx>.png so we can eyeball it.

import { config } from "dotenv";
import { existsSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
// Load from .env.local if present, else .env, else parent's .env
const envPath = ['.env.local', '.env', '../.env'].map(p => resolve(__dirname, '..', p)).find(p => existsSync(p));
if (envPath) {
  config({ path: envPath });
  console.log(`Loaded env from ${envPath}`);
}
if (!process.env.OPENAI_API_KEY) {
  console.error("OPENAI_API_KEY missing. Set it in carousel-studio/.env.local or export it.");
  process.exit(1);
}

const topicsHandler = (await import("../api/topics.js")).default;
const outlineHandler = (await import("../api/outline.js")).default;
const directionHandler = (await import("../api/direction.js")).default;
const promptSlideHandler = (await import("../api/prompt-slide.js")).default;
const renderSlideHandler = (await import("../api/render-slide.js")).default;

function mockReqRes(body) {
  let _status = 200, _json = null;
  const req = { method: "POST", body };
  const res = {
    status(c) { _status = c; return this; },
    json(o) { _json = o; return this; },
    setHeader() { return this; },
    end() { return this; },
  };
  return { req, res, get status() { return _status; }, get body() { return _json; } };
}

async function call(label, handler, body) {
  process.stdout.write(`→ ${label}…`);
  const t0 = Date.now();
  const m = mockReqRes(body);
  await handler(m.req, m.res);
  const dt = Date.now() - t0;
  if (m.status !== 200) {
    console.log(` FAIL [${m.status}] ${JSON.stringify(m.body).slice(0,200)}`);
    process.exit(1);
  }
  console.log(` ok [${dt}ms]`);
  return m.body;
}

const outDir = resolve(__dirname, "smoke-output");
if (!existsSync(outDir)) mkdirSync(outDir);

console.log(`\n=== CAROUSEL STUDIO · SMOKE TEST ===\n`);
console.log(`OPENAI_API_KEY: ${process.env.OPENAI_API_KEY.slice(0,7)}…`);
console.log(`GEMS_API_BASE: ${process.env.GEMS_API_BASE || '(default railway)'}`);
console.log(`IMAGE_MODEL: ${process.env.IMAGE_MODEL || 'gpt-image-2'}`);
console.log();

// 1. Topics
const POI = "Colosseum";
const THEME = "things tourists regret";
const topicsRes = await call("topics", topicsHandler, { poi: POI, theme: THEME, n_topics: 4 });
console.log(`  grounded: ${topicsRes.grounded} · ${topicsRes.n_clusters_seen} clusters seen`);
topicsRes.topics.forEach((t, i) => console.log(`  ${i+1}. ${t.title}\n     ${t.hook}\n     clusters: [${(t.source_cluster_ids||[]).join(',')}]`));
console.log();

// 2. Outline (pick first topic)
const topic = topicsRes.topics[0];
const outlineRes = await call("outline", outlineHandler, { poi: POI, topic, n_slides: 4 });
console.log(`  slides:`);
outlineRes.outline.slides.forEach((s, i) => console.log(`  ${i+1}. [${s.kind}] ${s.headline}${s.citation_cluster_id ? ` · #${s.citation_cluster_id}` : ''}`));
console.log();

// 3. Direction
const directionRes = await call("direction", directionHandler, { poi: POI, outline: outlineRes.outline });
const sb = directionRes.style_bible;
console.log(`  palette: ${sb.palette.name} (${sb.palette.bg} / ${sb.palette.fg} / ${sb.palette.accent})`);
console.log(`  type:    ${sb.type_pairing.name}`);
console.log(`  layouts: ${sb.layout_ids.join(', ')}`);
console.log(`  tone:    ${sb.tonal_direction}`);
console.log();

// 4. Prompt slide 0
const promptRes = await call("prompt-slide [0]", promptSlideHandler, {
  slide: outlineRes.outline.slides[0],
  style_bible: sb,
  slide_index: 0,
  total_slides: outlineRes.outline.slides.length,
});
console.log(`  layout: ${promptRes.layout_id}`);
console.log(`  prompt: ${promptRes.prompt.slice(0, 400)}${promptRes.prompt.length > 400 ? '…' : ''}`);
console.log();

// 5. Render slide 0 at LOW quality to save $$ in smoke test
const renderRes = await call("render-slide [0] low-quality", renderSlideHandler, {
  prompt: promptRes.prompt,
  size: "1024x1024",
  quality: "low",
});
console.log(`  image: ${renderRes.image_b64.length} chars b64 · cost ~$${renderRes.cost_usd} · ${renderRes.latency_ms}ms`);
const imgPath = resolve(outDir, "slide-0-low.png");
writeFileSync(imgPath, Buffer.from(renderRes.image_b64, 'base64'));
console.log(`  wrote: ${imgPath}`);
console.log();

console.log(`=== ALL FIVE AGENTS PASSED ===`);
console.log(`Eyeball the rendered slide at ${imgPath}`);

// Thin Express wrapper so the Vercel-style api/*.js handlers can also run on Railway / Render / any Node host.
// Vercel deploy ignores this file (zero-config detects api/*.js + public/).

import express from "express";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync } from "node:fs";
import { config as dotenvConfig } from "dotenv";

// Load local .env files in dev; on Railway env vars are injected directly.
const __envDirname = dirname(fileURLToPath(import.meta.url));
for (const f of [".env.local", ".env"]) {
  const p = resolve(__envDirname, f);
  if (existsSync(p)) { dotenvConfig({ path: p }); break; }
}

import topics from "./api/topics.js";
import outline from "./api/outline.js";
import direction from "./api/direction.js";
import promptSlide from "./api/prompt-slide.js";
import renderSlide from "./api/render-slide.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.json({ limit: "4mb" }));

// Wrap handlers so any async throw becomes a 500
const wrap = (h) => async (req, res, next) => {
  try { await h(req, res); }
  catch (e) { console.error(req.path, e); if (!res.headersSent) res.status(500).json({ error: String(e?.message || e) }); }
};

// CORS middleware (also handles preflight)
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (req.method === "OPTIONS") return res.status(204).end();
  next();
});

app.post("/api/topics", wrap(topics));
app.post("/api/outline", wrap(outline));
app.post("/api/direction", wrap(direction));
app.post("/api/prompt-slide", wrap(promptSlide));
app.post("/api/render-slide", wrap(renderSlide));

app.get("/api/health", (req, res) => {
  res.json({
    status: "ok",
    image_model: process.env.IMAGE_MODEL || "gpt-image-2",
    gems_api: process.env.GEMS_API_BASE || "https://gems-poi-production.up.railway.app",
    has_openai_key: !!process.env.OPENAI_API_KEY,
  });
});

// Static — served at root, /index.html, and any subpath
app.use(express.static(resolve(__dirname, "public"), { extensions: ["html"] }));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`carousel-studio listening on :${PORT}`);
  console.log(`  IMAGE_MODEL=${process.env.IMAGE_MODEL || "gpt-image-2"}`);
  console.log(`  GEMS_API_BASE=${process.env.GEMS_API_BASE || "(default railway)"}`);
});

// Client for the Gems POI API (the existing Flask service on Railway).
// All requests are best-effort: on failure we return null so the upstream agent
// can decide whether to fall back to pure-LLM ideation.

const BASE = () => (process.env.GEMS_API_BASE || "https://gems-poi-production.up.railway.app").replace(/\/+$/, "");

async function _post(path, body, timeout_ms = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout_ms);
  try {
    const r = await fetch(`${BASE()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

async function _get(path, timeout_ms = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout_ms);
  try {
    const r = await fetch(`${BASE()}${path}`, { signal: ctrl.signal });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

export async function searchClusters({ poi, query, intent_l1, limit = 8, min_tier = "B" }) {
  return _post("/api/search_clusters", { poi, query, intent_l1, limit, min_tier });
}

export async function explainCluster({ cluster_id, max_quotes = 4 }) {
  return _post("/api/explain_cluster", { cluster_id, max_quotes });
}

export async function poiStats(poi) {
  return _get(`/api/poi_stats?poi=${encodeURIComponent(poi)}`);
}

export async function subAttractions(poi, limit = 10) {
  return _get(`/api/sub_attractions?poi=${encodeURIComponent(poi)}&limit=${limit}`);
}

// POST /api/search_insights — per-row insight retrieval with structured filters.
export async function searchInsights({ poi, query, filters = {}, limit = 20 }) {
  return _post("/api/search_insights", { poi, query, filters, limit });
}

// Bulk-gather a TIP-CENTRIC evidence set: named sub-attractions, real positive
// recommendations with extracted numbers, and high-quality cluster anchors.
// This is the multi-modal sweep that lets the topic agent write specific,
// punchy, backed-by-data tips instead of generic listicle slop.
export async function gatherTipsEvidence(poi, queryHint = "") {
  const [subs, posTier, recommended, namedAnchors] = await Promise.all([
    // 1. Named sub-attractions with tip-worthy specificity (Mona Lisa, Roma Pass, Denon Wing, etc.)
    subAttractions(poi, 12),
    // 2. Top tier-A positive clusters across all L1s — broad spread of high-confidence praise
    searchClusters({ poi, query: queryHint || "best tips and recommendations", limit: 12, min_tier: "A" }),
    // 3. Positive recommendation insights WITH numeric anchors — these are the "save 30 min" / "arrive by 8:30" tips
    searchInsights({
      poi,
      query: queryHint || "tips and recommendations",
      filters: { intent_tag: ["recommendation"], insight_sentiment: ["positive"], has_numeric: true },
      limit: 20,
    }),
    // 4. Tier-A clusters that name specific entities (rooms, side entrances, named landmarks)
    searchClusters({ poi, query: "named places and specific entrances", intent_l1: "Attention Intelligence", limit: 8, min_tier: "A" }),
  ]);

  return {
    sub_attractions: (subs || [])
      .filter((s) => s && (s.recommendation_count || 0) >= 3 && (s.positive_count || 0) > (s.negative_count || 0) * 0.6)
      .slice(0, 10)
      .map((s) => ({
        name: s.sub_attraction,
        n_insights: s.n_insights,
        n_sources: s.n_sources,
        positive_count: s.positive_count,
        recommendation_count: s.recommendation_count,
        avg_minutes: s.avg_minutes,
        sample_cluster_ids: (s.sample_cluster_ids || []).slice(0, 3),
        top_intent_tags: s.top_intent_tags,
      })),
    tier_a_clusters: [...(posTier || []), ...(namedAnchors || [])]
      .filter((c) => c && (c.sentiment === "positive" || c.sentiment === "mixed"))
      .slice(0, 20)
      .map((c) => ({
        cluster_id: c.cluster_id,
        name: c.name,
        l1: c.l1,
        l2: c.l2,
        sentiment: c.sentiment,
        size: c.size,
        n_sources: c.n_sources,
        quality_tier: c.quality_tier,
        anchor_entities: c.anchor_entities,
      })),
    numeric_recommendations: (recommended || []).slice(0, 18).map((i) => ({
      cluster_id: i.cluster_id,
      cluster_name: i.cluster_name,
      sub_attraction: i.sub_attraction,
      raw_text: (i.raw_text || i.text || "").slice(0, 300),
      numeric_anchors: i.numeric_anchors,
      rating: i.rating,
      source: i.source,
      source_id: i.source_id,
      l1: i.l1,
    })),
  };
}

// Legacy gatherEvidence kept for backward compat — now delegates to gatherTipsEvidence
// but returns the old flat array shape for callers that only want cluster summaries.
export async function gatherEvidence(poi, queryHint = "") {
  const rich = await gatherTipsEvidence(poi, queryHint);
  const seen = new Set();
  const flat = [];
  for (const c of rich.tier_a_clusters) {
    if (seen.has(c.cluster_id)) continue;
    seen.add(c.cluster_id);
    flat.push(c);
  }
  return flat;
}

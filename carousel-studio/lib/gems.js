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

// Bulk-gather a diverse evidence set for a POI: fans out one search per L1 intent.
// Returns a flat array of cluster summaries with intent_l1 tagged.
export async function gatherEvidence(poi, queryHint = "") {
  const L1s = [
    "Visit Intelligence",
    "Attention Intelligence",
    "Operational Intelligence",
    "Discovery Intelligence",
    "Culinary Intelligence",
  ];
  const results = await Promise.all(
    L1s.map((l1) => searchClusters({ poi, query: queryHint || l1.toLowerCase(), intent_l1: l1, limit: 6, min_tier: "B" }))
  );
  const out = [];
  results.forEach((r, i) => {
    if (!r || !Array.isArray(r)) return;
    for (const c of r) {
      out.push({
        cluster_id: c.cluster_id,
        name: c.name,
        l1: c.l1 || L1s[i],
        l2: c.l2,
        sentiment: c.sentiment,
        size: c.size,
        n_sources: c.n_sources,
        quality_tier: c.quality_tier,
        anchor_entities: c.anchor_entities,
      });
    }
  });
  // De-dupe by cluster_id
  const seen = new Set();
  return out.filter((c) => {
    if (seen.has(c.cluster_id)) return false;
    seen.add(c.cluster_id);
    return true;
  });
}

"""Evidence quote sampler.

For a cluster_id, walks member_indices → flat_insights → raw_*.json by source_id
to surface the actual reviewer text. Returns the trust trail for the cluster.
"""
from typing import Optional

from . import config
from .store import load_all


def _trim(text: str, max_chars: int = config.QUOTE_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text.rfind(" ", 0, max_chars)
    if cut < int(max_chars * 0.6):
        cut = max_chars
    return text[:cut].rstrip() + "…"


def _make_card_only(c: dict) -> dict:
    return {
        "cluster_id": c["cluster_id"],
        "poi_name": c["poi_name"],
        "name": c["name"],
        "l1": c.get("l1", ""),
        "l2": c.get("l2", ""),
        "l3": c.get("l3", ""),
        "secondary_l1": c.get("secondary_l1"),
        "sentiment": c.get("sentiment", "neutral"),
        "quality_tier": c.get("quality_tier", "C"),
        "size": int(c.get("size", 0)),
        "n_sources": int(c.get("n_sources", 1)),
        "source_mix": c.get("source_mix", {}),
        "anchor_entities": c.get("anchor_entities", []),
        "relevance_score": 0.0,
        "cluster_uri": f"mcp://gems/cluster/{c['cluster_id']}",
    }


def explain(cluster_id: int, max_quotes: int = 5) -> dict:
    s = load_all()
    if cluster_id not in s.clusters_by_id:
        raise ValueError(f"unknown cluster_id: {cluster_id}")
    cluster = s.clusters_by_id[cluster_id]

    centroid_idx = cluster.get("centroid_text_indices") or []
    member_idx = cluster.get("member_indices") or []
    ordered = centroid_idx + [i for i in member_idx if i not in centroid_idx]

    quotes: list[dict] = []
    seen_sources: dict[str, int] = {}
    skipped_for_diversity: list[dict] = []

    for idx in ordered:
        if idx < 0 or idx >= len(s.insights):
            continue
        ins = s.insights[idx]
        source = ins.get("source")
        source_id = ins.get("source_id")
        raw_lookup = s.raw.get(source, {})
        raw_row = raw_lookup.get(source_id)
        if raw_row is None:
            continue
        raw_text = raw_row.get("text", "")
        if not raw_text:
            continue

        quote = {
            "source": source,
            "raw_text": _trim(raw_text),
            "rating": raw_row.get("rating"),
            "source_id": source_id,
            "source_url": raw_row.get("source_url"),
            "anchor_entity": ins.get("anchor_entity"),
        }

        # prefer one-per-source on first pass; backfill duplicates afterward
        if source in seen_sources:
            skipped_for_diversity.append(quote)
            continue
        seen_sources[source] = 1
        quotes.append(quote)
        if len(quotes) >= max_quotes:
            break

    if len(quotes) < max_quotes:
        for q in skipped_for_diversity:
            quotes.append(q)
            if len(quotes) >= max_quotes:
                break

    return {
        "cluster": _make_card_only(cluster),
        "quotes": quotes,
    }

"""Hybrid search/rank/filter over cluster index.

Ranking design (industry best practices):
  1. Two-stage retrieval: cheap recall (vector cosine top-K), expensive rerank
  2. Normalized signals in [0, 1] combined linearly
  3. Sigmoid-saturated size (replaces log) so mega-clusters don't run away
  4. Lexical anchor bonus (BM25-light) — embeddings miss literal entity matches
  5. Intent-aware sentiment boost (positive query × positive cluster gets +ε)
  6. MMR (Maximal Marginal Relevance) at output for diversity

search(poi, query, intent_l1?, limit, min_tier) → list[ClusterCard]
list_concerns(poi, category?, limit) → list[ClusterCard]
list_highlights(poi, intent?, limit) → list[ClusterCard]
"""
import math
import os
import re
from typing import Any, Optional

import numpy as np

from l2.llm_client import get_openai

from . import config
from .store import load_all


def _embed_query(query: str) -> np.ndarray:
    client = get_openai()
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=[query])
    v = np.array(resp.data[0].embedding, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _make_card(c: dict, relevance: Optional[float] = None) -> dict:
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
        "relevance_score": float(relevance) if relevance is not None else 0.0,
        "cluster_uri": f"mcp://gems/cluster/{c['cluster_id']}",
    }


def _size_sigmoid(size: int) -> float:
    """Smooth saturation — size 50 ≈ 0.95, size 200 ≈ 1.0, size 5 ≈ 0.18.
    Avoids the log-trap where size 300 still beats size 50 noticeably.
    """
    x = (size - config.SIZE_SIGMOID_CENTER) / config.SIZE_SIGMOID_SCALE
    return 1.0 / (1.0 + math.exp(-x))


def _detect_intent(query: str) -> Optional[str]:
    """Returns 'positive', 'negative', or None based on intent tokens in query."""
    q = query.lower()
    pos_hits = any(tok in q for tok in config.POSITIVE_INTENT_TOKENS)
    neg_hits = any(tok in q for tok in config.NEGATIVE_INTENT_TOKENS)
    if pos_hits and not neg_hits:
        return "positive"
    if neg_hits and not pos_hits:
        return "negative"
    return None


def _intent_penalty(query_intent: Optional[str], cluster: dict) -> float:
    """Score haircut when query intent clashes with cluster sentiment.

    Returns 1.0 (full penalty triggers) on clash, 0.0 otherwise. Caller multiplies
    by INTENT_MISMATCH_PENALTY for the score subtraction. Conservative — only
    downweights misalignment, never positively boosts (which over-rewards big
    aligned clusters).
    """
    if query_intent is None:
        return 0.0
    sent = cluster.get("sentiment", "neutral")
    if query_intent == "positive" and sent in {"negative", "mixed"}:
        return 1.0
    if query_intent == "negative" and sent == "positive":
        return 1.0
    return 0.0


def _anchor_bonus(query: str, cluster: dict) -> float:
    """Lexical-match boost: if any anchor_entity appears in the query as a token-bounded
    substring, contribute the bonus weight. BM25-light — fixes the case where the user
    types 'Venus' or 'Roma Pass' literally and we want the cluster carrying that entity.
    """
    anchors = cluster.get("anchor_entities") or []
    if not anchors:
        return 0.0
    q_lower = query.lower()
    poi_lower = (cluster.get("poi_name") or "").lower()
    for a in anchors:
        if not a:
            continue
        a_lower = a.lower()
        # Skip the POI name itself as an anchor — every cluster has it; not informative
        if a_lower == poi_lower:
            continue
        # word-boundary check — "Mona Lisa" matches in "is the Mona Lisa worth it"
        if re.search(rf"\b{re.escape(a_lower)}\b", q_lower):
            return 1.0  # gated by config weight in caller
    return 0.0


def _hybrid_score(
    cosine: float,
    tier: str,
    n_sources: int,
    size: int,
    anchor_match: float,
    intent_clash: float = 0.0,
) -> float:
    """Linear combination of normalized retrieval signals minus intent-clash haircut.

    cosine kept raw (typical embedding cosines are [0.3, 0.9]) — normalizing to
    [0,1] compresses distinctions and lets other signals override semantic match.
    """
    tier_w = config.TIER_WEIGHT.get(tier, 0.3)
    src_div = min(n_sources / 5.0, 1.0)
    size_sat = _size_sigmoid(size)
    w = config.RANK_WEIGHTS
    return (
        w["cosine"] * cosine
        + w["tier"] * tier_w
        + w["source_diversity"] * src_div
        + w["size_sigmoid"] * size_sat
        + w["anchor_bonus"] * anchor_match
        - config.INTENT_MISMATCH_PENALTY * intent_clash
    )


def _intent_matches(c: dict, intent_l1: Optional[str]) -> bool:
    if intent_l1 is None:
        return True
    return c.get("l1") == intent_l1 or c.get("secondary_l1") == intent_l1


def _tier_passes(c: dict, min_tier: str) -> bool:
    return config.TIER_RANK.get(c.get("quality_tier", "C"), 99) <= config.TIER_RANK.get(min_tier, 2)


def _mmr_select(
    candidates: list[tuple[float, int, np.ndarray]],
    limit: int,
    lambda_: float = config.MMR_LAMBDA,
) -> list[tuple[float, int]]:
    """MMR rerank for output diversity — gated.

    Strategy: ALWAYS keep position #1 as the highest-scored candidate (never displace
    a clear winner). Apply MMR only for positions 2..limit, and only when the gap
    between #1 and #2 raw scores is below MMR_GAP_TRIGGER (signals genuine tie/dup
    redundancy that diversity should break). Otherwise pure relevance ranking.
    """
    if not candidates:
        return []
    sorted_cands = sorted(candidates, key=lambda x: -x[0])
    top = sorted_cands[0]
    if len(sorted_cands) < 2 or limit <= 1:
        return [(top[0], top[1])]

    gap = top[0] - sorted_cands[1][0]
    if gap >= config.MMR_GAP_TRIGGER:
        # Clear winner — pure relevance.
        return [(c[0], c[1]) for c in sorted_cands[:limit]]

    # Tight top — apply MMR only for positions 2+ to break redundancy.
    selected: list[tuple[float, int]] = [(top[0], top[1])]
    selected_vecs: list[np.ndarray] = [top[2]]
    remaining = list(sorted_cands[1:])

    while remaining and len(selected) < limit:
        best_mmr = -1e9
        best_i = -1
        for i, (rel, emb_idx, vec) in enumerate(remaining):
            sims_to_selected = np.array([float(vec @ sv) for sv in selected_vecs])
            redundancy = float(sims_to_selected.max())
            mmr = lambda_ * rel - (1.0 - lambda_) * redundancy
            if mmr > best_mmr:
                best_mmr = mmr
                best_i = i
        rel_best, emb_best, vec_best = remaining.pop(best_i)
        selected.append((rel_best, emb_best))
        selected_vecs.append(vec_best)

    return selected


def search(
    poi: str,
    query: str,
    intent_l1: Optional[str] = None,
    limit: int = 5,
    min_tier: str = "B",
) -> list[dict]:
    if poi not in config.ALLOWED_POIS:
        raise ValueError(f"poi must be one of {sorted(config.ALLOWED_POIS)}; got {poi!r}")
    if intent_l1 is not None and intent_l1 not in config.VALID_L1:
        raise ValueError(f"intent_l1 must be one of {sorted(config.VALID_L1)}; got {intent_l1!r}")
    if min_tier not in config.TIER_RANK:
        raise ValueError(f"min_tier must be A/B/C; got {min_tier!r}")

    s = load_all()
    if s.embeddings is None:
        raise RuntimeError(
            "Cluster embeddings not built. Run `.venv/bin/python -u l2/build_index.py`."
        )

    candidate_indices = [
        i
        for i, m in enumerate(s.emb_meta)
        if m["poi_name"] == poi
        and _intent_matches(s.clusters_by_id[m["cluster_id"]], intent_l1)
        and _tier_passes(s.clusters_by_id[m["cluster_id"]], min_tier)
    ]
    if not candidate_indices:
        return []

    q = _embed_query(query)
    query_intent = _detect_intent(query)
    sub = s.embeddings[candidate_indices]
    sims = sub @ q

    top_k = min(config.TOP_K_RERANK, len(candidate_indices))
    top = np.argsort(-sims)[:top_k]

    rich: list[tuple[float, int, np.ndarray]] = []
    for local_i in top:
        emb_idx = candidate_indices[int(local_i)]
        cluster = s.clusters_by_id[s.emb_meta[emb_idx]["cluster_id"]]
        cos = float(sims[int(local_i)])
        score = _hybrid_score(
            cosine=cos,
            tier=cluster.get("quality_tier", "C"),
            n_sources=int(cluster.get("n_sources", 1)),
            size=int(cluster.get("size", 0)),
            anchor_match=_anchor_bonus(query, cluster),
            intent_clash=_intent_penalty(query_intent, cluster),
        )
        rich.append((score, emb_idx, s.embeddings[emb_idx]))

    rich.sort(key=lambda x: -x[0])
    # MMR over the top 2×limit so diversity has something to choose from
    pool = rich[: max(limit * 2, 10)]
    selected = _mmr_select(pool, limit=limit)

    out: list[dict] = []
    for score, emb_idx in selected:
        cluster = s.clusters_by_id[s.emb_meta[emb_idx]["cluster_id"]]
        out.append(_make_card(cluster, relevance=round(score, 4)))
    return out


def list_concerns(
    poi: str,
    category: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    if poi not in config.ALLOWED_POIS:
        raise ValueError(f"poi must be one of {sorted(config.ALLOWED_POIS)}; got {poi!r}")

    s = load_all()
    candidates = [
        c
        for c in s.clusters
        if c["poi_name"] == poi
        and (
            c.get("l1") == "Operational Intelligence"
            or c.get("secondary_l1") == "Operational Intelligence"
        )
        and c.get("sentiment") in config.NEGATIVE_SENTIMENTS
        and (category is None or c.get("l2") == category)
    ]
    candidates.sort(
        key=lambda c: -(int(c.get("size", 0)) * min(int(c.get("n_sources", 1)) / 5.0, 1.0))
    )
    return [_make_card(c) for c in candidates[:limit]]


def _make_insight_card(
    ins: dict,
    flat_idx: int,
    cluster: Optional[dict],
    relevance: float,
) -> dict:
    """Tier-2 insight card — granular per-row payload joined with parent cluster name.

    Cluster is optional: if the insight isn't linked to a labelled cluster (orphan
    row), we still surface the insight with cluster_id=None.
    """
    return {
        "flat_idx": int(flat_idx),
        "poi_name": ins.get("poi_name", ""),
        "text": ins.get("insight_text") or ins.get("text") or "",
        "source": ins.get("source", ""),
        "source_id": ins.get("source_id"),
        "rating": ins.get("rating"),
        "l1": ins.get("l1") or (cluster.get("l1") if cluster else ""),
        "l2": ins.get("l2") or (cluster.get("l2") if cluster else ""),
        "l3": ins.get("l3") or (cluster.get("l3") if cluster else ""),
        "insight_sentiment": ins.get("insight_sentiment") or ins.get("sentiment"),
        "intent_tag": ins.get("intent_tag"),
        "numeric_anchors": ins.get("numeric_anchors") or [],
        "sub_attraction": ins.get("sub_attraction"),
        "cluster_id": ins.get("cluster_id") if ins.get("cluster_id") is not None
        else (cluster.get("cluster_id") if cluster else None),
        "cluster_name": cluster.get("name") if cluster else None,
        "relevance_score": round(float(relevance), 4),
    }


def _insight_passes_filters(ins: dict, filters: dict) -> bool:
    """AND-combined filter pass. Unknown keys are ignored; missing fields fail
    the filter (conservative — we'd rather under-recall than smuggle in a row
    that doesn't actually match the user's intent).
    """
    if not filters:
        return True

    l1 = filters.get("l1")
    if l1 is not None and ins.get("l1") != l1:
        return False

    l2 = filters.get("l2")
    if l2 is not None and ins.get("l2") != l2:
        return False

    rmin = filters.get("rating_min")
    if rmin is not None:
        r = ins.get("rating")
        if r is None or int(r) < int(rmin):
            return False

    rmax = filters.get("rating_max")
    if rmax is not None:
        r = ins.get("rating")
        if r is None or int(r) > int(rmax):
            return False

    sources = filters.get("source")
    if sources:
        if ins.get("source") not in set(sources):
            return False

    sentiments = filters.get("insight_sentiment")
    if sentiments:
        s = ins.get("insight_sentiment") or ins.get("sentiment")
        if s not in set(sentiments):
            return False

    intents = filters.get("intent_tag")
    if intents:
        tag = ins.get("intent_tag")
        if tag not in set(intents):
            return False

    if filters.get("has_numeric") is True:
        na = ins.get("numeric_anchors")
        if not na:
            return False

    sub = filters.get("sub_attraction")
    if sub:
        sa = (ins.get("sub_attraction") or "")
        if sub.lower() not in sa.lower():
            return False

    cid = filters.get("cluster_id")
    if cid is not None:
        if ins.get("cluster_id") != cid:
            return False

    min_fresh = filters.get("min_freshness")
    if min_fresh is not None:
        f = ins.get("freshness_score")
        if f is None or float(f) < float(min_fresh):
            return False

    return True


def search_insights(
    poi: str,
    query: str,
    filters: Optional[dict] = None,
    limit: int = 10,
) -> list[dict]:
    """Tier-2 retrieval — per-insight semantic search with structured filters.

    Embeds `query`, cosine-ranks against the per-insight embedding matrix, then
    nudges by parent-cluster tier weight + insight freshness. Filters are
    AND-combined and applied BEFORE ranking so we don't waste TOP_K slots on
    rows that will be discarded anyway.
    """
    if poi not in config.ALLOWED_POIS:
        raise ValueError(f"poi must be one of {sorted(config.ALLOWED_POIS)}; got {poi!r}")
    filters = filters or {}

    s = load_all()
    if s.insight_embeddings is None or not s.insight_emb_meta:
        raise FileNotFoundError(
            "Per-insight embeddings missing. Build them (e.g. "
            "`.venv/bin/python -u l2/build_insight_index.py`) to enable search_insights."
        )

    # Candidate prefilter — POI + structured filters. Each candidate carries
    # (insight, flat_idx, row_index_in_insight_embeddings).
    candidates: list[tuple[dict, int, int]] = []
    for row_i, meta in enumerate(s.insight_emb_meta):
        flat_idx = meta.get("flat_idx")
        if flat_idx is None:
            continue
        if not (0 <= flat_idx < len(s.insights)):
            continue
        ins = s.insights[flat_idx]
        if ins.get("poi_name") != poi:
            continue
        if not _insight_passes_filters(ins, filters):
            continue
        candidates.append((ins, int(flat_idx), row_i))

    if not candidates:
        return []

    q = _embed_query(query)
    row_indices = np.array([c[2] for c in candidates], dtype=np.int64)
    sub = s.insight_embeddings[row_indices]
    sims = sub @ q  # already normalized on both sides

    top_k = min(config.TOP_K_RERANK, len(candidates))
    top_local = np.argsort(-sims)[:top_k]

    w = config.INSIGHT_RANK_WEIGHTS
    scored: list[tuple[float, dict, int]] = []
    for li in top_local:
        ins, flat_idx, _ = candidates[int(li)]
        cluster = None
        cid = ins.get("cluster_id")
        if cid is not None:
            cluster = s.clusters_by_id.get(cid)
        tier = (cluster.get("quality_tier", "C") if cluster else "C")
        tier_w = config.TIER_WEIGHT.get(tier, 0.3)
        fresh = float(ins.get("freshness_score") or 0.0)
        score = (
            w["cosine"] * float(sims[int(li)])
            + w["cluster_tier"] * tier_w
            + w["freshness"] * fresh
        )
        scored.append((score, ins, flat_idx))

    scored.sort(key=lambda x: -x[0])

    out: list[dict] = []
    for score, ins, flat_idx in scored[:limit]:
        cluster = None
        cid = ins.get("cluster_id")
        if cid is not None:
            cluster = s.clusters_by_id.get(cid)
        out.append(_make_insight_card(ins, flat_idx, cluster, relevance=score))
    return out


def list_highlights(
    poi: str,
    intent: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    if poi not in config.ALLOWED_POIS:
        raise ValueError(f"poi must be one of {sorted(config.ALLOWED_POIS)}; got {poi!r}")
    if intent is not None and intent not in config.VALID_L1:
        raise ValueError(f"intent must be one of {sorted(config.VALID_L1)}; got {intent!r}")

    s = load_all()
    candidates = [
        c
        for c in s.clusters
        if c["poi_name"] == poi
        and c.get("quality_tier") == "A"
        and c.get("sentiment") in config.POSITIVE_SENTIMENTS
        and (intent is None or c.get("l1") == intent or c.get("secondary_l1") == intent)
    ]
    candidates.sort(
        key=lambda c: -_hybrid_score(
            cosine=1.0,
            tier=c.get("quality_tier", "C"),
            n_sources=int(c.get("n_sources", 1)),
            size=int(c.get("size", 0)),
            anchor_match=0.0,
        )
    )
    return [_make_card(c) for c in candidates[:limit]]

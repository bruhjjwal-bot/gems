"""In-memory store loaders for clusters, embeddings, insights, and raw quotes.

Loaded once at server startup. Re-runs of Path A regenerate the underlying JSON;
the server can be restarted to pick them up.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from . import config


@dataclass
class Store:
    clusters: list[dict] = field(default_factory=list)
    clusters_by_id: dict[int, dict] = field(default_factory=dict)
    insights: list[dict] = field(default_factory=list)
    raw: dict[str, dict[str, dict]] = field(default_factory=dict)  # source -> source_id -> raw row
    embeddings: Optional[np.ndarray] = None
    emb_meta: list[dict] = field(default_factory=list)
    emb_index_by_cluster: dict[int, int] = field(default_factory=dict)
    # --- Tier-2 (per-insight) retrieval surface ---
    insight_embeddings: Optional[np.ndarray] = None
    insight_emb_meta: list[dict] = field(default_factory=list)
    # flat_idx -> row index inside insight_embeddings (aligned with insight_emb_meta)
    insight_emb_index_by_flat_idx: dict[int, int] = field(default_factory=dict)
    pois: list[str] = field(default_factory=list)
    last_built_at: Optional[str] = None


_store: Optional[Store] = None


def _load_raw(source: str, path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = json.loads(path.read_text())
    lookup: dict[str, dict] = {}
    for r in rows:
        # TA legacy schema uses review_id; others use source_id
        sid = r.get("source_id") or r.get("review_id")
        if sid:
            lookup[sid] = r
    return lookup


def load_all(force: bool = False) -> Store:
    global _store
    if _store is not None and not force:
        return _store

    s = Store()
    if not config.CLUSTERS_PATH.exists():
        raise FileNotFoundError(
            f"Missing {config.CLUSTERS_PATH}. Run Path A pipeline first."
        )

    s.clusters = json.loads(config.CLUSTERS_PATH.read_text())
    s.clusters_by_id = {c["cluster_id"]: c for c in s.clusters}
    s.pois = sorted({c["poi_name"] for c in s.clusters if c["poi_name"] in config.ALLOWED_POIS})
    s.last_built_at = datetime.fromtimestamp(
        config.CLUSTERS_PATH.stat().st_mtime
    ).isoformat(timespec="seconds")

    # Prefer the enriched flat insights file (richer per-row metadata for tier-2
    # filtering) when present, else fall back to the canonical flat_insights_all.
    if config.FLAT_INSIGHTS_ENRICHED_PATH.exists():
        s.insights = json.loads(config.FLAT_INSIGHTS_ENRICHED_PATH.read_text())
    elif config.FLAT_INSIGHTS_PATH.exists():
        s.insights = json.loads(config.FLAT_INSIGHTS_PATH.read_text())

    for src, path in config.RAW_PATHS.items():
        s.raw[src] = _load_raw(src, path)

    if config.EMBEDDINGS_PATH.exists() and config.EMBEDDINGS_META_PATH.exists():
        s.embeddings = np.load(config.EMBEDDINGS_PATH).astype(np.float32)
        s.emb_meta = json.loads(config.EMBEDDINGS_META_PATH.read_text())
        s.emb_index_by_cluster = {m["cluster_id"]: i for i, m in enumerate(s.emb_meta)}
        norms = np.linalg.norm(s.embeddings, axis=1, keepdims=True) + 1e-9
        s.embeddings = s.embeddings / norms

    # Per-insight embeddings — optional, only required by the tier-2 tool.
    if (
        config.INSIGHT_EMBEDDINGS_PATH.exists()
        and config.INSIGHT_EMBEDDINGS_META_PATH.exists()
    ):
        ie = np.load(config.INSIGHT_EMBEDDINGS_PATH).astype(np.float32)
        meta = json.loads(config.INSIGHT_EMBEDDINGS_META_PATH.read_text())
        norms = np.linalg.norm(ie, axis=1, keepdims=True) + 1e-9
        s.insight_embeddings = ie / norms
        s.insight_emb_meta = meta
        # meta rows are aligned 1:1 with the embeddings matrix rows.
        s.insight_emb_index_by_flat_idx = {
            int(m["flat_idx"]): i for i, m in enumerate(meta) if "flat_idx" in m
        }

    _store = s
    return s


def reset():
    global _store
    _store = None

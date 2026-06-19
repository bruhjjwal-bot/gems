"""MCP server config — POI allowlist, ranking weights, file paths."""
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

CLUSTERS_PATH = DATA_DIR / "labelled_clusters_all.json"
FLAT_INSIGHTS_PATH = DATA_DIR / "flat_insights_all.json"
FLAT_INSIGHTS_ENRICHED_PATH = DATA_DIR / "flat_insights_enriched.json"
EMBEDDINGS_PATH = DATA_DIR / "cluster_embeddings.npy"
EMBEDDINGS_META_PATH = DATA_DIR / "cluster_embeddings_meta.json"
INSIGHT_EMBEDDINGS_PATH = DATA_DIR / "insight_embeddings.npy"
INSIGHT_EMBEDDINGS_META_PATH = DATA_DIR / "insight_embeddings_meta.json"

RAW_PATHS = {
    "tripadvisor_review": DATA_DIR / "raw_reviews.json",
    "google_review": DATA_DIR / "raw_google_review.json",
    "reddit_post": DATA_DIR / "raw_reddit_post.json",
    "reddit_comment": DATA_DIR / "raw_reddit_comment.json",
    "youtube_transcript_chunk": DATA_DIR / "raw_youtube_transcript_chunk.json",
}

ALLOWED_POIS = {"Louvre Museum", "Colosseum"}

VALID_L1 = {
    "Visit Intelligence",
    "Attention Intelligence",
    "Discovery Intelligence",
    "Culinary Intelligence",
    "Operational Intelligence",
}

EMBED_MODEL = "text-embedding-3-small"

RANK_WEIGHTS = {
    "cosine": 0.55,
    "tier": 0.25,
    "source_diversity": 0.15,
    "size_sigmoid": 0.05,
    "anchor_bonus": 0.10,  # lexical-match bonus, additive (not part of 1.0 budget)
}

# Sigmoid size saturation: 1 / (1 + exp(-(size - center)/scale))
# Saturates near 1 for size > ~50, 0.5 at size=center, near 0 for size < ~5.
# Replaces log() which never saturated cleanly — mega-clusters kept growing.
SIZE_SIGMOID_CENTER = 20
SIZE_SIGMOID_SCALE = 10

# MMR diversity rerank — applied ONLY when the top-K relevance scores are close
# (gap < MMR_GAP_TRIGGER between #1 and #2). Avoids displacing a clear winner.
MMR_LAMBDA = 0.85          # high relevance weight; light diversity touch
MMR_GAP_TRIGGER = 0.05     # only apply MMR if top-2 scores within this gap

# Intent-sentiment alignment — applies a small score haircut when the query intent
# clashes with the cluster sentiment. Conservative downweight, not a positive boost,
# to avoid over-rewarding the popularity tail.
POSITIVE_INTENT_TOKENS = {
    "worth", "best", "should i see", "recommend", "highlight", "must",
    "favorite", "favourite", "great", "love", "amazing", "prioritize",
    "priority", "hidden", "gem", "seek",
}
NEGATIVE_INTENT_TOKENS = {
    "avoid", "scam", "rude", "bad", "warning", "complaint", "problem",
    "tourist trap", "ripoff", "ripped off", "dirty", "annoying",
}
INTENT_MISMATCH_PENALTY = 0.04

TIER_WEIGHT = {"A": 1.0, "B": 0.6, "C": 0.3}
TIER_RANK = {"A": 0, "B": 1, "C": 2}  # lower = better; for min_tier filter

# Tier-2 (per-insight search) ranking — cosine dominates, tier + freshness are
# small additive nudges. We don't reuse RANK_WEIGHTS because the cluster-level
# diversity/size signals don't apply per insight.
INSIGHT_RANK_WEIGHTS = {
    "cosine": 1.0,
    "cluster_tier": 0.10,   # multiplied by TIER_WEIGHT of the parent cluster
    "freshness": 0.05,      # multiplied by freshness_score in [0,1]
}

QUOTE_MAX_CHARS = 400
TOP_K_RERANK = 50

NEGATIVE_SENTIMENTS = {"negative", "mixed"}
POSITIVE_SENTIMENTS = {"positive"}

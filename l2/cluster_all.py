"""Cross-source clustering: merge all insights_*.json + insights.json (TA),
tag each with source, embed once, cluster per POI across sources.

Tracks `source_mix` per cluster: counter of how many insights came from each platform.
This is the artifact that tests the hypothesis 'different platforms surface different insights'.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import numpy as np

from l2.embed_cluster import embed_all, cluster_one_poi, top_k_nearest_centroid

DATA_DIR = Path(__file__).parent / "data"
OUT_FLAT = DATA_DIR / "flat_insights_all.json"
OUT_CLUSTERS = DATA_DIR / "clusters_all.json"

INSIGHTS_FILES = {
    "tripadvisor_review": DATA_DIR / "insights.json",  # original TA pipeline output
    "google_review": DATA_DIR / "insights_google_review.json",
    # v2: youtube_comment dropped entirely (low-signal fan-talk)
    "youtube_transcript_chunk": DATA_DIR / "insights_youtube_transcript_chunk.json",
    "reddit_post": DATA_DIR / "insights_reddit_post.json",
    "reddit_comment": DATA_DIR / "insights_reddit_comment.json",
    "firecrawl_blog": DATA_DIR / "insights_firecrawl_blog.json",
    "reddit_targeted": DATA_DIR / "insights_reddit_targeted.json",
}

# v2: tightened threshold + post-merge anchor-overlap pass
DIST_THRESHOLD_V2 = 0.28
POST_MERGE_CENTROID_DIST = 0.25

# v2.1 mega-cluster guards
POST_MERGE_HARD_CAP = 150         # refuse merges that would balloon past this
POST_MERGE_COHERENCE_MAX = 0.45   # refuse merges where any existing member is far from candidate centroid

# Anchor entities too generic to count as anchor-overlap evidence
GENERIC_ANCHOR_BLOCKLIST = {
    "colosseum", "the colosseum",
    "louvre", "louvre museum", "the louvre",
    "rome", "paris",
}


def _norm_anchor(a) -> str:
    return (a or "").strip().lower()


def _cluster_anchors(group: list[int], flat: list[dict]) -> set[str]:
    out: set[str] = set()
    for idx in group:
        a = _norm_anchor(flat[idx].get("anchor_entity"))
        if not a or a in GENERIC_ANCHOR_BLOCKLIST:
            continue
        out.add(a)
    return out


def _centroid(group: list[int], vectors: np.ndarray) -> np.ndarray:
    v = vectors[group].mean(axis=0)
    n = np.linalg.norm(v) + 1e-9
    return v / n


def post_merge_groups(groups: list[list[int]], vectors: np.ndarray, flat: list[dict]) -> list[list[int]]:
    """Greedy post-merge: two clusters merge if anchor_entity sets overlap AND centroid cosine distance < threshold."""
    if len(groups) <= 1:
        return groups
    # Precompute anchors + centroids
    anchors = [_cluster_anchors(g, flat) for g in groups]
    centroids = [_centroid(g, vectors) for g in groups]
    alive = list(range(len(groups)))
    merged_into: dict[int, int] = {}

    changed = True
    while changed:
        changed = False
        for i_idx in range(len(alive)):
            i = alive[i_idx]
            if i in merged_into:
                continue
            for j_idx in range(i_idx + 1, len(alive)):
                j = alive[j_idx]
                if j in merged_into:
                    continue
                if not anchors[i] or not anchors[j]:
                    continue
                if not (anchors[i] & anchors[j]):
                    continue
                dist = 1.0 - float(np.dot(centroids[i], centroids[j]))
                if dist >= POST_MERGE_CENTROID_DIST:
                    continue

                # v2.1 HARD CAP: refuse merges that would create a mega-cluster
                combined_size = len(groups[i]) + len(groups[j])
                if combined_size > POST_MERGE_HARD_CAP:
                    continue

                # v2.1 COHERENCE GUARD: refuse merges where any existing member
                # of either cluster is too far from the prospective merged centroid.
                # candidate centroid = combined centroid of i ∪ j (normalised).
                combined = groups[i] + groups[j]
                cand_centroid = _centroid(combined, vectors)
                member_vecs = vectors[combined]
                # cosine distance per member = 1 - (member · centroid). Take the worst (max).
                max_member_dist = 1.0 - float((member_vecs @ cand_centroid).min())
                if max_member_dist > POST_MERGE_COHERENCE_MAX:
                    continue

                # merge j into i
                groups[i] = combined
                anchors[i] = anchors[i] | anchors[j]
                centroids[i] = cand_centroid
                merged_into[j] = i
                changed = True
        alive = [k for k in alive if k not in merged_into]

    return [groups[k] for k in alive]


def flatten_with_source(reviews: list[dict], default_source: str) -> list[dict]:
    """Flatten per-review insights into one-row-per-insight, tag with source."""
    flat = []
    for r in reviews:
        source = r.get("source", default_source)
        for ins in r.get("insights", []):
            flat.append({
                "source": source,
                "source_id": r.get("source_id") or r.get("review_id"),
                "source_uuid": r.get("source_uuid") or r.get("review_uuid"),
                "poi_id": r["poi_id"],
                "poi_name": r["poi_name"],
                "rating": r.get("rating"),
                "text": ins["text"],
                "strength": ins.get("strength", 0.5),
                "anchor_entity": ins.get("anchor_entity"),
                "meta": r.get("meta", {}),
            })
    return flat


def main():
    print("=== Cross-source clustering ===")
    all_flat: list[dict] = []
    for default_source, path in INSIGHTS_FILES.items():
        if not path.exists():
            print(f"  [skip] {default_source}: {path} missing")
            continue
        reviews = json.loads(path.read_text())
        sub = flatten_with_source(reviews, default_source)
        print(f"  {default_source:28s} {len(reviews):4d} rows → {len(sub):4d} insights")
        all_flat.extend(sub)

    print(f"\nTotal: {len(all_flat)} insights across all sources")
    OUT_FLAT.write_text(json.dumps(all_flat, indent=2, default=str))

    print(f"\nEmbedding {len(all_flat)} insights...")
    t0 = time.time()
    vectors = embed_all([f["text"] for f in all_flat])
    print(f"  shape={vectors.shape}  in {time.time()-t0:.1f}s")

    # Per-POI clustering, cross-source
    by_poi: dict[str, list[int]] = {}
    for i, f in enumerate(all_flat):
        by_poi.setdefault(f["poi_name"], []).append(i)

    clusters_out: list[dict] = []
    cluster_id = 0
    for poi_name, indices in by_poi.items():
        print(f"\nClustering {len(indices)} insights for {poi_name}...")
        groups = cluster_one_poi(indices, vectors, threshold=DIST_THRESHOLD_V2)
        groups.sort(key=lambda g: -len(g))
        print(f"  → {len(groups)} clusters (pre-merge). Top 10 sizes: {[len(g) for g in groups[:10]]}")
        groups = post_merge_groups(groups, vectors, all_flat)
        groups.sort(key=lambda g: -len(g))
        print(f"  → {len(groups)} clusters (post-merge). Top 10 sizes: {[len(g) for g in groups[:10]]}")
        for g in groups:
            top5 = top_k_nearest_centroid(g, vectors)
            poi_id = all_flat[g[0]]["poi_id"]
            # Source mix
            source_mix: dict[str, int] = {}
            for idx in g:
                src = all_flat[idx]["source"]
                source_mix[src] = source_mix.get(src, 0) + 1
            clusters_out.append({
                "cluster_id": cluster_id,
                "poi_id": poi_id,
                "poi_name": poi_name,
                "size": len(g),
                "member_indices": g,
                "centroid_text_indices": top5,
                "source_mix": source_mix,
                "n_sources": len(source_mix),
            })
            cluster_id += 1

    OUT_CLUSTERS.write_text(json.dumps(clusters_out, indent=2, default=str))
    print(f"\nTotal clusters: {len(clusters_out)}")

    # Quick source-mix stats
    from collections import Counter
    n_sources_dist = Counter(c["n_sources"] for c in clusters_out)
    print(f"n_sources_per_cluster: {dict(n_sources_dist)}")
    multi = [c for c in clusters_out if c["n_sources"] >= 2]
    print(f"Multi-source clusters: {len(multi)}/{len(clusters_out)} ({100*len(multi)/len(clusters_out):.1f}%)")

    print(f"\nWrote {OUT_CLUSTERS}")


if __name__ == "__main__":
    main()

"""Step 2a+2b: embed all insights then per-POI agglomerative clustering.

Output: l2/data/clusters.json
[
  {
    "cluster_id": int,
    "poi_id": str, "poi_name": str,
    "size": int,
    "member_indices": [int, ...],   # indices into the flat insights array
    "centroid_text_indices": [int, ...]  # top-5 nearest to centroid (for naming)
  },
  ...
]

And: l2/data/flat_insights.json — flat array of all individual insights with lineage.
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
from sklearn.cluster import AgglomerativeClustering

from l2.llm_client import get_openai

EMBED_MODEL = "text-embedding-3-small"
DIST_THRESHOLD = 0.35

IN_PATH = Path(__file__).parent / "data" / "insights.json"
FLAT_PATH = Path(__file__).parent / "data" / "flat_insights.json"
CLUSTERS_PATH = Path(__file__).parent / "data" / "clusters.json"


def flatten(reviews: list[dict]) -> list[dict]:
    """Flatten per-review insights into one-row-per-insight with lineage."""
    flat = []
    for r in reviews:
        for ins in r.get("insights", []):
            flat.append({
                "review_id": r["review_id"],
                "review_uuid": r["review_uuid"],
                "poi_id": r["poi_id"],
                "poi_name": r["poi_name"],
                "rating": r.get("rating"),
                "helpful_count": r.get("helpful_count", 0),
                "text": ins["text"],
                "strength": ins.get("strength", 0.5),
                "anchor_entity": ins.get("anchor_entity"),
            })
    return flat


def embed_all(texts: list[str], batch_size: int = 1024) -> np.ndarray:
    """Get embeddings in batches. text-embedding-3-small returns 1536-d."""
    client = get_openai()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  Embedding batch {i//batch_size + 1} ({len(batch)} texts)...")
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        vectors.extend([d.embedding for d in resp.data])
    return np.array(vectors)


def cluster_one_poi(indices: list[int], vectors: np.ndarray, threshold: float = DIST_THRESHOLD) -> list[list[int]]:
    """Return list of clusters, each cluster is a list of global indices."""
    if len(indices) < 2:
        return [indices] if indices else []
    sub = vectors[indices]
    clf = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        linkage="average",
        metric="cosine",
    )
    labels = clf.fit_predict(sub)
    out: dict[int, list[int]] = {}
    for local_i, lbl in enumerate(labels):
        out.setdefault(int(lbl), []).append(indices[local_i])
    return list(out.values())


def top_k_nearest_centroid(member_indices: list[int], vectors: np.ndarray, k: int = 5) -> list[int]:
    if not member_indices:
        return []
    sub = vectors[member_indices]
    centroid = sub.mean(axis=0)
    centroid /= (np.linalg.norm(centroid) + 1e-9)
    sub_norm = sub / (np.linalg.norm(sub, axis=1, keepdims=True) + 1e-9)
    sims = sub_norm @ centroid
    order = np.argsort(-sims)
    return [member_indices[int(i)] for i in order[:k]]


def main():
    print("=== Step 2: Embed + Cluster ===")
    reviews = json.loads(IN_PATH.read_text())
    flat = flatten(reviews)
    print(f"Flattened to {len(flat)} insights")
    FLAT_PATH.write_text(json.dumps(flat, indent=2, default=str))

    print(f"Embedding {len(flat)} insights with {EMBED_MODEL}...")
    t0 = time.time()
    vectors = embed_all([f["text"] for f in flat])
    print(f"  done in {time.time()-t0:.1f}s, shape={vectors.shape}")

    # Per-POI clustering
    by_poi: dict[str, list[int]] = {}
    for i, f in enumerate(flat):
        by_poi.setdefault(f["poi_name"], []).append(i)

    all_clusters: list[dict] = []
    cluster_id = 0
    for poi_name, indices in by_poi.items():
        print(f"\nClustering {len(indices)} insights for {poi_name}...")
        groups = cluster_one_poi(indices, vectors)
        groups.sort(key=lambda g: -len(g))
        print(f"  → {len(groups)} clusters, sizes: {[len(g) for g in groups[:10]]}{' ...' if len(groups) > 10 else ''}")
        for g in groups:
            top5 = top_k_nearest_centroid(g, vectors)
            poi_id = flat[g[0]]["poi_id"]
            all_clusters.append({
                "cluster_id": cluster_id,
                "poi_id": poi_id,
                "poi_name": poi_name,
                "size": len(g),
                "member_indices": g,
                "centroid_text_indices": top5,
            })
            cluster_id += 1

    CLUSTERS_PATH.write_text(json.dumps(all_clusters, indent=2, default=str))
    print(f"\nTotal clusters: {len(all_clusters)}")
    print(f"Wrote {CLUSTERS_PATH}")


if __name__ == "__main__":
    main()

"""Build the cluster-name embedding index.

Reads labelled_clusters_all.json, embeds each cluster's `name` via
text-embedding-3-small, writes:
  - data/cluster_embeddings.npy  (n_clusters, 1536) float32
  - data/cluster_embeddings_meta.json  [{cluster_id, poi_name}, ...] aligned

Idempotent — run after every Path A pipeline rebuild.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import numpy as np

from l2.llm_client import get_openai

EMBED_MODEL = "text-embedding-3-small"
BATCH = 1024

DATA = Path(__file__).parent / "data"
CLUSTERS = DATA / "labelled_clusters_all.json"
OUT_NPY = DATA / "cluster_embeddings.npy"
OUT_META = DATA / "cluster_embeddings_meta.json"


def embed(texts: list[str]) -> np.ndarray:
    client = get_openai()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        print(f"  Embedding batch {i // BATCH + 1} ({len(batch)} texts)...")
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        vectors.extend([d.embedding for d in resp.data])
    return np.array(vectors, dtype=np.float32)


def main():
    print("=== Building cluster embedding index ===")
    clusters = json.loads(CLUSTERS.read_text())
    print(f"Loaded {len(clusters)} clusters from {CLUSTERS.name}")

    meta = [{"cluster_id": c["cluster_id"], "poi_name": c["poi_name"]} for c in clusters]
    texts = [c["name"] for c in clusters]
    vecs = embed(texts)
    assert vecs.shape[0] == len(clusters), f"Got {vecs.shape[0]} vecs for {len(clusters)} clusters"
    print(f"Embedded: shape={vecs.shape}, dtype={vecs.dtype}")

    np.save(OUT_NPY, vecs)
    OUT_META.write_text(json.dumps(meta, indent=2))
    size_mb = OUT_NPY.stat().st_size / 1024 / 1024
    print(f"Wrote {OUT_NPY} ({size_mb:.1f} MB) and {OUT_META}")


if __name__ == "__main__":
    main()

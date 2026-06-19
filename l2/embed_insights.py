"""Step: per-insight embeddings with text-embedding-3-small.

Reads `l2/data/flat_insights_enriched.json` (preferred) or falls back to
`l2/data/flat_insights_all.json`. Embeds each row's `text` field, batched.

Outputs:
  - `l2/data/insight_embeddings.npy`        (n_insights, 1536) float32
  - `l2/data/insight_embeddings_meta.json`  aligned metadata per row
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

from l2.llm_client import get_openai

EMBED_MODEL = "text-embedding-3-small"
BATCH_SIZE = 1024
EMBED_DIM = 1536

DATA_DIR = Path(__file__).parent / "data"
ENRICHED_PATH = DATA_DIR / "flat_insights_enriched.json"
FALLBACK_PATH = DATA_DIR / "flat_insights_all.json"
OUT_NPY = DATA_DIR / "insight_embeddings.npy"
OUT_META = DATA_DIR / "insight_embeddings_meta.json"


def load_input() -> tuple[list[dict], Path]:
    if ENRICHED_PATH.exists():
        path = ENRICHED_PATH
    elif FALLBACK_PATH.exists():
        print(
            f"WARN: {ENRICHED_PATH.name} not found; falling back to {FALLBACK_PATH.name}",
            file=sys.stderr,
        )
        path = FALLBACK_PATH
    else:
        raise FileNotFoundError(
            f"Neither {ENRICHED_PATH} nor {FALLBACK_PATH} exists."
        )
    rows = json.loads(path.read_text())
    print(f"Loaded {len(rows)} insights from {path.name}")
    return rows, path


def embed_all(texts: list[str], batch_size: int = BATCH_SIZE) -> np.ndarray:
    """Embed texts in batches. text-embedding-3-small returns 1536-d."""
    client = get_openai()
    vectors: list[list[float]] = []
    n_batches = (len(texts) + batch_size - 1) // batch_size
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        b_idx = i // batch_size + 1
        print(f"  Embedding batch {b_idx}/{n_batches} ({len(batch)} texts)...")
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        vectors.extend([d.embedding for d in resp.data])
    arr = np.array(vectors, dtype=np.float32)
    return arr


def build_meta(rows: list[dict]) -> list[dict]:
    meta = []
    for flat_idx, r in enumerate(rows):
        meta.append({
            "flat_idx": flat_idx,
            "source_id": r.get("source_id"),
            "poi_name": r.get("poi_name"),
            "cluster_id": r.get("cluster_id"),
        })
    return meta


def human_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def main():
    print("=== Embed insights ===")
    rows, src_path = load_input()

    texts = [(r.get("text") or "") for r in rows]
    missing = sum(1 for t in texts if not t.strip())
    if missing:
        print(f"WARN: {missing} rows have empty `text`; embedding empty string for those.")

    print(f"Embedding {len(texts)} insights with {EMBED_MODEL} (batch={BATCH_SIZE})...")
    t0 = time.time()
    vectors = embed_all(texts)
    dt = time.time() - t0
    print(f"  done in {dt:.1f}s, shape={vectors.shape}, dtype={vectors.dtype}")

    assert vectors.shape == (len(rows), EMBED_DIM), (
        f"Unexpected shape {vectors.shape}, expected ({len(rows)}, {EMBED_DIM})"
    )

    meta = build_meta(rows)

    np.save(OUT_NPY, vectors)
    OUT_META.write_text(json.dumps(meta, indent=2, default=str))

    npy_size = OUT_NPY.stat().st_size
    meta_size = OUT_META.stat().st_size

    print()
    print(f"Final shape: {vectors.shape}")
    print(f"Wrote {OUT_NPY} ({human_size(npy_size)})")
    print(f"Wrote {OUT_META} ({human_size(meta_size)})")


if __name__ == "__main__":
    main()

"""Score cross-source clusters (specificity + sentiment + tier).

Same logic as score.py but reads/writes _all files and carries source_mix forward.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from l2.score import score_batch, BATCH_SIZE

WORKERS = 10
IN_PATH = Path(__file__).parent / "data" / "named_clusters_all.json"
OUT_PATH = Path(__file__).parent / "data" / "scored_clusters_all.json"


def main():
    print("=== Scoring cross-source clusters ===")
    clusters = json.loads(IN_PATH.read_text())
    batches = [clusters[i:i + BATCH_SIZE] for i in range(0, len(clusters), BATCH_SIZE)]
    print(f"Scoring {len(clusters)} clusters in {len(batches)} batches ({WORKERS} workers)...")

    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(score_batch, b): b for b in batches}
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            all_results.extend(res)
            print(f"  [{i}/{len(batches)}] batch done ({len(res)} clusters)")

    OUT_PATH.write_text(json.dumps(all_results, indent=2, default=str))

    from collections import Counter
    spec = Counter(c["specificity"] for c in all_results)
    sent = Counter(c["sentiment"] for c in all_results)
    tier = Counter(c["quality_tier"] for c in all_results)
    print(f"\nSpecificity: {dict(spec)} | Sentiment: {dict(sent)} | Tier: {dict(tier)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

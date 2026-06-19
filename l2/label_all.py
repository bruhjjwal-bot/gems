"""Label cross-source clusters with approved L1/L2/L3 taxonomy.

Same logic as label.py but reads/writes _all files and carries source_mix forward.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from l2.label import label_batch, BATCH_SIZE
from l2.taxonomy import load_taxonomy, build_lookups, taxonomy_for_prompt

WORKERS = 10
IN_PATH = Path(__file__).parent / "data" / "scored_clusters_all.json"
OUT_PATH = Path(__file__).parent / "data" / "labelled_clusters_all.json"


def main():
    print("=== Labelling cross-source clusters ===")
    combos = load_taxonomy()
    lookups = build_lookups(combos)
    tax_str = taxonomy_for_prompt(combos)
    print(f"Loaded {len(combos)} valid (L1,L2,L3) combos")

    clusters = json.loads(IN_PATH.read_text())
    batches = [clusters[i:i + BATCH_SIZE] for i in range(0, len(clusters), BATCH_SIZE)]
    print(f"Labelling {len(clusters)} clusters in {len(batches)} batches ({WORKERS} workers)...")

    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(label_batch, b, tax_str, lookups): b for b in batches}
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            all_results.extend(res)
            print(f"  [{i}/{len(batches)}] batch done ({len(res)} labels)")

    OUT_PATH.write_text(json.dumps(all_results, indent=2, default=str))

    from collections import Counter
    l1_counts = Counter(c.get("l1", "?") for c in all_results)
    valid = sum(1 for c in all_results if c.get("label_valid"))
    low_conf = sum(1 for c in all_results if c.get("label_confidence", 0) < 0.7)
    n_multi = sum(1 for c in all_results if c.get("n_sources", 1) >= 2)
    print(f"\nValid: {valid}/{len(all_results)} | Low-conf (<0.7): {low_conf} | Multi-source: {n_multi}/{len(all_results)}")
    print(f"L1 distribution: {dict(l1_counts)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

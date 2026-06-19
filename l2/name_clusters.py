"""Step 2c: name each cluster with a canonical 10-25 word generalised statement."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from l2.llm_client import call_json

MODEL = "gpt-4o"
WORKERS = 10
IN_FLAT = Path(__file__).parent / "data" / "flat_insights.json"
IN_CLUSTERS = Path(__file__).parent / "data" / "clusters.json"
OUT_PATH = Path(__file__).parent / "data" / "named_clusters.json"

SYSTEM = (
    "You are an attraction intelligence clustering engine. "
    "Given representative insights from a cluster, produce ONE canonical cluster name. "
    "Output strict JSON only."
)

USER_TEMPLATE = """POI: {poi_name}
Cluster size: {size} insights

Representative insights (sorted by closeness to cluster centroid):
{insights}

Produce ONE canonical cluster name that captures the underlying observation across these insights.

RULES:
1. 10-25 words. One sentence.
2. Preserve named entities (rooms, artworks, restaurants, viewpoints, sub-attractions) if present.
3. Generalise: "X is praised" / "X is recommended" / "Visitors often regret Y" / "Z is reported as overrated".
   If size >= 3, you may use "frequently" — that frequency is real.
4. Make sense standalone (without reading members).
5. Cluster names should NOT include first-person, anecdotes, or "I/we/me".
6. AVOID generic catchalls like "the visit was great" or "people enjoyed it".

Return JSON:
{{
  "cluster_name": "<the canonical sentence>",
  "anchor_entities": ["<named entities preserved>", ...],   // empty list if none
  "confidence": 0.0   // 0.0-1.0 how confident this name captures the cluster
}}
"""


def name_one(cluster: dict, flat: list[dict]) -> dict:
    poi_name = cluster["poi_name"]
    size = cluster["size"]
    top = cluster["centroid_text_indices"]
    insights_text = "\n".join(
        f"  {i+1}. {flat[idx]['text']}"
        for i, idx in enumerate(top)
    )
    user = USER_TEMPLATE.format(poi_name=poi_name, size=size, insights=insights_text)
    t0 = time.time()
    try:
        out = call_json(SYSTEM, user, model=MODEL, temperature=0.2, max_tokens=300)
        return {
            **cluster,
            "name": str(out.get("cluster_name", "")).strip(),
            "anchor_entities": out.get("anchor_entities") or [],
            "name_confidence": float(out.get("confidence", 0.7) or 0.7),
            "name_elapsed_s": round(time.time() - t0, 2),
        }
    except Exception as e:
        # Fallback: use the centroid-nearest insight verbatim
        fallback = flat[top[0]]["text"] if top else ""
        return {
            **cluster,
            "name": fallback,
            "anchor_entities": [],
            "name_confidence": 0.0,
            "name_error": str(e),
            "name_elapsed_s": round(time.time() - t0, 2),
        }


def main():
    print("=== Step 2c: Name Clusters ===")
    flat = json.loads(IN_FLAT.read_text())
    clusters = json.loads(IN_CLUSTERS.read_text())
    print(f"Naming {len(clusters)} clusters with {MODEL} ({WORKERS} workers)...")

    results: list[dict] = []
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(name_one, c, flat): c for c in clusters}
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            results.append(res)
            if i % 25 == 0 or i == len(clusters):
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                print(f"  [{i}/{len(clusters)}] {rate:.1f} clusters/s")

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nDone. Wrote {OUT_PATH}")

    # Show top 20 by size
    print("\nTop 20 clusters by size:")
    for c in sorted(results, key=lambda x: -x["size"])[:20]:
        print(f"  [{c['poi_name'][:15]:15s}] size={c['size']:3d} conf={c['name_confidence']:.2f}  {c['name']}")


if __name__ == "__main__":
    main()

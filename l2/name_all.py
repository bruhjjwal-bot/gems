"""Name cross-source clusters. Mirrors name_clusters.py but reads
flat_insights_all.json + clusters_all.json and writes named_clusters_all.json.

Adds source-mix awareness to the naming prompt so the model knows when
a cluster is corroborated across platforms.
"""
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
IN_FLAT = Path(__file__).parent / "data" / "flat_insights_all.json"
IN_CLUSTERS = Path(__file__).parent / "data" / "clusters_all.json"
OUT_PATH = Path(__file__).parent / "data" / "named_clusters_all.json"

SYSTEM = (
    "You are an attraction intelligence clustering engine. "
    "Given representative insights from a cluster, produce ONE canonical cluster name. "
    "Output strict JSON only."
)

USER_TEMPLATE = """POI: {poi_name}
Cluster size: {size} insights | Sources: {source_mix}

Representative insights (sorted by closeness to cluster centroid):
{insights}

Produce ONE canonical cluster name that captures the underlying observation.

RULES:
1. 10-25 words. One sentence.
2. Preserve named entities (rooms, artworks, restaurants, viewpoints, sub-attractions) if present in members.
3. Generalise tone. If size >= 3, you may use "frequently" — that frequency is real.
4. Standalone (makes sense without reading members).
5. No first-person, no anecdote, no "I/we/me".
6. Avoid generic catchalls.
7. POLARITY: assess whether the insights share polarity. If the cluster mixes praise and criticism, set polarity="mixed" and write a DUAL-POLARITY name (e.g., "Visitors split: praise X but criticize Y"). Otherwise polarity is "positive", "negative", or "neutral".

Return JSON:
{{
  "cluster_name": "<the canonical sentence>",
  "anchor_entities": ["<named entities preserved>", ...],
  "polarity": "positive|negative|neutral|mixed",
  "confidence": 0.0
}}
"""


def name_one(cluster: dict, flat: list[dict]) -> dict:
    poi_name = cluster["poi_name"]
    size = cluster["size"]
    top = cluster["centroid_text_indices"]
    source_mix = cluster.get("source_mix", {})
    insights_text = "\n".join(
        f"  {i+1}. [{flat[idx]['source']}] {flat[idx]['text']}"
        for i, idx in enumerate(top)
    )
    user = USER_TEMPLATE.format(
        poi_name=poi_name,
        size=size,
        source_mix=", ".join(f"{k}={v}" for k, v in sorted(source_mix.items(), key=lambda kv: -kv[1])),
        insights=insights_text,
    )
    t0 = time.time()
    try:
        out = call_json(SYSTEM, user, model=MODEL, temperature=0.2, max_tokens=300)
        return {
            **cluster,
            "name": str(out.get("cluster_name", "")).strip(),
            "anchor_entities": out.get("anchor_entities") or [],
            "polarity": (out.get("polarity") or "neutral").strip().lower(),
            "name_confidence": float(out.get("confidence", 0.7) or 0.7),
            "name_elapsed_s": round(time.time() - t0, 2),
        }
    except Exception as e:
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
    print("=== Naming cross-source clusters ===")
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
            if i % 50 == 0 or i == len(clusters):
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                print(f"  [{i}/{len(clusters)}] {rate:.1f} clusters/s")

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {OUT_PATH}")

    # Show top 20 multi-source clusters
    multi = sorted([c for c in results if c.get("n_sources", 1) >= 2], key=lambda x: -x["size"])[:20]
    print(f"\nTop 20 MULTI-SOURCE clusters (size + cross-platform corroboration):")
    for c in multi:
        sm = ", ".join(f"{k}={v}" for k, v in sorted(c.get("source_mix", {}).items(), key=lambda kv: -kv[1]))
        print(f"  [{c['poi_name'][:13]:13s} size={c['size']:3d} sources={c.get('n_sources',1)} ({sm})] {c['name'][:75]}")


if __name__ == "__main__":
    main()

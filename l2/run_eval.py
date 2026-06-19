"""Phase 0 runner: sample 60 rows, label each with open-ended prompt, save JSON."""
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
from l2.prompts import build_phase0_prompt
from l2.sampling import sample_all

OUT_PATH = Path(__file__).parent / "eval_results.json"
WORKERS = 10


def label_row(row: dict) -> dict:
    system, user = build_phase0_prompt(
        poi_name=row["poi_name"] or "(unknown POI)",
        city=row["city"] or "",
        source=row["source"],
        text=row["text"],
    )
    t0 = time.time()
    try:
        out = call_json(system, user, model="gpt-4o", temperature=0.1, max_tokens=1200)
        out["__elapsed_s"] = round(time.time() - t0, 2)
        out["__error"] = None
    except Exception as e:
        out = {"__error": str(e), "__elapsed_s": round(time.time() - t0, 2)}
    return {
        "input": {k: v for k, v in row.items() if k != "text"} | {"text_preview": row["text"][:200]},
        "output": out,
    }


def main():
    print("Sampling 60 rows across 5 sources...")
    rows = sample_all()
    print(f"Got {len(rows)} rows:")
    src_counts: dict[str, int] = {}
    poi_counts: dict[str, int] = {}
    for r in rows:
        src_counts[r["source"]] = src_counts.get(r["source"], 0) + 1
        poi_counts[r["poi_name"] or "?"] = poi_counts.get(r["poi_name"] or "?", 0) + 1
    for s, c in src_counts.items():
        print(f"  {s}: {c}")
    print(f"  POI distribution: {poi_counts}")
    print()

    print(f"Calling gpt-4o with {WORKERS} parallel workers...")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(label_row, r): r for r in rows}
        done = 0
        for f in as_completed(futures):
            row = futures[f]
            result = f.result()
            results.append(result)
            done += 1
            err = result["output"].get("__error")
            if err:
                print(f"  [{done}/{len(rows)}] [ERROR] {row['source']} {row['poi_name'][:20]}: {err}")
            else:
                l1 = result["output"].get("l1", "?")
                l2 = result["output"].get("l2", "?")
                conf = result["output"].get("confidence", 0)
                print(f"  [{done}/{len(rows)}] {row['source']:25s} {row['poi_name'][:15]:15s} → {l1}/{l2}  conf={conf:.2f}")

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {len(results)} results to {OUT_PATH}")


if __name__ == "__main__":
    main()

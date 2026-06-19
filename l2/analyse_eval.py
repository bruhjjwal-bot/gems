"""Phase 0 analysis: cluster the 60 open-ended labels to surface a taxonomy."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

IN_PATH = Path(__file__).parent / "eval_results.json"


def main():
    data = json.loads(IN_PATH.read_text())
    rows = [d for d in data if not d["output"].get("__error")]
    errors = [d for d in data if d["output"].get("__error")]

    print(f"=== Eval summary ===")
    print(f"Total: {len(data)} | Successful: {len(rows)} | Errors: {len(errors)}")
    if errors:
        print(f"\nFirst 3 errors:")
        for e in errors[:3]:
            print(f"  {e['input']['source']} {e['input'].get('poi_name','?')}: {e['output']['__error']}")
    if not rows:
        return

    # L1 distribution
    l1_counts = Counter(r["output"].get("l1") for r in rows)
    print(f"\n=== L1 candidates ({len(l1_counts)} unique) ===")
    for l1, c in l1_counts.most_common():
        print(f"  {c:3d}  {l1}")

    # L2 per L1
    l2_per_l1: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        l1 = r["output"].get("l1") or "?"
        l2 = r["output"].get("l2") or "?"
        l2_per_l1[l1][l2] += 1
    print(f"\n=== L2 within each L1 ===")
    for l1, counts in sorted(l2_per_l1.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"\n  L1={l1}")
        for l2, c in counts.most_common():
            print(f"    {c:3d}  {l2}")

    # L3 distribution
    l3_counts = Counter(r["output"].get("l3") for r in rows if r["output"].get("l3"))
    print(f"\n=== L3 candidates ({len(l3_counts)} unique) ===")
    for l3, c in l3_counts.most_common(20):
        print(f"  {c:3d}  {l3}")

    # major_bulk
    mb_counts = Counter(r["output"].get("major_bulk") for r in rows)
    print(f"\n=== major_bulk values ({len(mb_counts)} unique) ===")
    for mb, c in mb_counts.most_common():
        print(f"  {c:3d}  {mb}")

    # minor_bulk
    minor_counts = Counter()
    for r in rows:
        for m in (r["output"].get("minor_bulk") or []):
            minor_counts[m] += 1
    print(f"\n=== minor_bulk top 30 ({len(minor_counts)} unique) ===")
    for m, c in minor_counts.most_common(30):
        print(f"  {c:3d}  {m}")

    # suggested_taxonomy
    suggested = [r["output"].get("suggested_taxonomy") for r in rows]
    suggested = [s for s in suggested if s and s.strip()]
    sug_counts = Counter(suggested)
    print(f"\n=== suggested_taxonomy (gaps the model wants filled) ===")
    print(f"Rows where model proposed a different path: {len(suggested)}/{len(rows)}")
    for s, c in sug_counts.most_common(15):
        print(f"  {c:3d}  {s}")

    # sentiment
    sent = Counter(r["output"].get("overall_sentiment") for r in rows)
    print(f"\n=== overall_sentiment ===")
    for s, c in sent.most_common():
        print(f"  {c:3d}  {s}")

    # confidence
    confs = [r["output"].get("confidence", 0) or 0 for r in rows]
    if confs:
        avg_conf = sum(confs) / len(confs)
        low_conf = [c for c in confs if c < 0.65]
        print(f"\n=== confidence ===")
        print(f"  avg: {avg_conf:.2f}  | low (<0.65): {len(low_conf)}/{len(confs)}")

    # is_relevant
    relevant = sum(1 for r in rows if r["output"].get("is_relevant"))
    print(f"\n=== is_relevant ===")
    print(f"  relevant: {relevant}/{len(rows)}  | irrelevant: {len(rows)-relevant}")

    # Per-source confidence
    by_src: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_src[r["input"]["source"]].append(r["output"].get("confidence", 0) or 0)
    print(f"\n=== per-source avg confidence ===")
    for src, cs in by_src.items():
        print(f"  {src:30s} avg={sum(cs)/len(cs):.2f}  n={len(cs)}")

    print()


if __name__ == "__main__":
    main()

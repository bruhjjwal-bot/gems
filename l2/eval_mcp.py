"""Golden-query eval harness for the MCP retrieval layer.

Runs each golden query through search_poi_advice (top-5) + explain_cluster on top hits,
and checks:
  (a) top-3 L1 hit — any cluster in top-3 has expected L1 (primary or secondary)
      (recall@3 is standard for RAG eval — MCP returns top-K, not just top-1)
  (b) text hit — ≥1 must_mention substring appears in any top-5 cluster name OR
      any evidence quote text from the top hit's explanation

Target: ≥75% pass rate to ship v1.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from l2.mcp_server.evidence import explain
from l2.mcp_server.retrieval import search

QUERIES_PATH = Path(__file__).parent / "mcp_server" / "golden_queries.json"


def _ci_contains_any(haystack: str, needles: list[str]) -> tuple[bool, list[str]]:
    hay = (haystack or "").lower()
    matched = [n for n in needles if n.lower() in hay]
    return (len(matched) > 0, matched)


def run():
    queries = json.loads(QUERIES_PATH.read_text())
    print(f"=== Running {len(queries)} golden queries ===\n")

    # Filter to cluster-tier queries — insight-tier queries (tier=='insight')
    # are evaluated separately by eval_insights.py
    cluster_queries = [q for q in queries if q.get("tier") != "insight"]
    print(f"  (skipping {len(queries) - len(cluster_queries)} insight-tier queries — separate eval)")

    results = []
    n_pass = 0
    for i, q in enumerate(cluster_queries, 1):
        hits = search(
            poi=q["poi"],
            query=q["query"],
            limit=5,
            min_tier="C",
        )
        expected_l1 = q["expected_l1"]
        must_mention = q["must_mention"]

        # Recall@3 for L1 — standard RAG eval metric
        l1_hit = any(
            h.get("l1") == expected_l1 or h.get("secondary_l1") == expected_l1
            for h in hits[:3]
        )

        text_corpus = " ".join(h.get("name", "") for h in hits)
        # Evidence from top-3 (an agent would typically inspect more than top-1)
        for h in hits[:3]:
            try:
                exp = explain(cluster_id=h["cluster_id"], max_quotes=3)
                text_corpus += " " + " ".join(qt.get("raw_text", "") for qt in exp.get("quotes", []))
            except Exception as e:
                text_corpus += f" [explain error: {e}]"

        text_hit, matched_terms = _ci_contains_any(text_corpus, must_mention)
        passed = l1_hit and text_hit
        if passed:
            n_pass += 1

        top_name = hits[0]["name"] if hits else "(no hits)"
        top_l1 = hits[0].get("l1") if hits else None
        print(
            f"[{i:2d}/{len(cluster_queries)}] {'PASS' if passed else 'FAIL'}  "
            f"{q['poi']:14s}  L1={'✓' if l1_hit else '✗'} text={'✓' if text_hit else '✗'}  "
            f"matched={matched_terms}"
        )
        print(f"        Q: {q['query']}")
        print(f"        Top hit ({top_l1}): {top_name[:100]}\n")

        results.append({
            "query": q["query"],
            "poi": q["poi"],
            "expected_l1": expected_l1,
            "passed": passed,
            "l1_hit": l1_hit,
            "text_hit": text_hit,
            "matched_terms": matched_terms,
            "top_hit": top_name if hits else None,
            "top_l1": top_l1,
            "n_hits": len(hits),
        })

    pct = 100 * n_pass / len(cluster_queries)
    print("\n" + "=" * 60)
    print(f"OVERALL: {n_pass}/{len(cluster_queries)} passed ({pct:.1f}%)")
    print("Target: ≥75% to ship v1")
    print("=" * 60)

    out = {"summary": {"pass": n_pass, "total": len(cluster_queries), "pct": round(pct, 1)}, "results": results}
    (Path(__file__).parent / "data" / "eval_mcp_results.json").write_text(json.dumps(out, indent=2))
    print(f"\nFull results: l2/data/eval_mcp_results.json")
    return pct


if __name__ == "__main__":
    sys.exit(0 if run() >= 75.0 else 1)

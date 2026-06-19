"""Step 3: score each cluster on specificity + sentiment, derive quality_tier."""
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
BATCH_SIZE = 20

IN_PATH = Path(__file__).parent / "data" / "named_clusters.json"
OUT_PATH = Path(__file__).parent / "data" / "scored_clusters.json"

SYSTEM = (
    "You are an attraction intelligence scoring engine. "
    "For each cluster, score named_entity_score (1-5), actionability_score (1-5), and infer cluster-level sentiment. "
    "Output strict JSON only."
)

USER_TEMPLATE = """Score each cluster on TWO independent axes plus sentiment:

NAMED_ENTITY_SCORE (1-5) — how specifically named is the subject?
5 = clearly identifiable named entity (Mona Lisa, Arch of Constantine, Café Marly, Roma Pass)
4 = named feature/area (upper terrace, Denon Wing, Palatine Hill)
3 = category of thing (street food vendors, smaller galleries)
2 = broad area (the museum, the ground floor)
1 = generic, no entity (the visit, the trip)

ACTIONABILITY_SCORE (1-5) — how directly does it tell you what to do?
5 = explicit action with constraint ("book 30 days ahead via official site to avoid markup")
4 = clear directive ("arrive before 9am for shorter queues")
3 = recommendation with target ("the Denon Wing is worth prioritising")
2 = soft suggestion ("the museum rewards a slow pace")
1 = pure observation, no action implied

SENTIMENT:
positive — recommendation, praise, hidden gem framing
negative — regret, complaint, warning
neutral — factual, informational
mixed — both positive and negative elements

CLUSTERS TO SCORE:
{clusters_json}

Return JSON with this exact shape:
{{
  "scores": [
    {{"cluster_id": <int>, "named_entity_score": <1-5>, "actionability_score": <1-5>, "sentiment": "positive|neutral|negative|mixed", "reasoning": "<one short phrase>"}}
  ]
}}
"""


def derive_tier(named_entity_score: int, actionability_score: int, size: int) -> str:
    """v2 tier rule on two axes."""
    if ((named_entity_score >= 4 or actionability_score >= 4) and size >= 3) or size >= 15:
        return "A"
    if named_entity_score >= 3 or actionability_score >= 3 or size >= 8:
        return "B"
    return "C"


def score_batch(batch: list[dict]) -> list[dict]:
    clusters_json = json.dumps(
        [{"cluster_id": c["cluster_id"], "name": c["name"], "size": c["size"]} for c in batch],
        indent=1,
    )
    user = USER_TEMPLATE.format(clusters_json=clusters_json)
    t0 = time.time()
    try:
        out = call_json(SYSTEM, user, model=MODEL, temperature=0.1, max_tokens=2000)
        scores = out.get("scores") or []
        score_by_id = {s["cluster_id"]: s for s in scores if isinstance(s, dict)}
        results = []
        for c in batch:
            s = score_by_id.get(c["cluster_id"]) or {}
            # v2: two axes
            nes = int(s.get("named_entity_score", s.get("specificity", 3)) or 3)
            act = int(s.get("actionability_score", s.get("specificity", 3)) or 3)
            specificity = max(nes, act)  # backwards-compat
            sentiment = s.get("sentiment", "neutral") or "neutral"
            reasoning = s.get("reasoning", "") or ""
            tier = derive_tier(nes, act, c["size"])
            results.append({
                **c,
                "named_entity_score": nes,
                "actionability_score": act,
                "specificity": specificity,
                "sentiment": sentiment,
                "score_reasoning": reasoning,
                "quality_tier": tier,
                "score_elapsed_s": round(time.time() - t0, 2),
            })
        return results
    except Exception as e:
        return [{**c, "named_entity_score": 3, "actionability_score": 3, "specificity": 3, "sentiment": "neutral", "quality_tier": "C", "score_error": str(e)} for c in batch]


def main():
    print("=== Step 3: Score Clusters ===")
    clusters = json.loads(IN_PATH.read_text())
    print(f"Scoring {len(clusters)} clusters in batches of {BATCH_SIZE} with {WORKERS} workers...")

    batches = [clusters[i:i + BATCH_SIZE] for i in range(0, len(clusters), BATCH_SIZE)]
    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(score_batch, b): b for b in batches}
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            all_results.extend(res)
            print(f"  [{i}/{len(batches)}] batch done ({len(res)} clusters)")

    OUT_PATH.write_text(json.dumps(all_results, indent=2, default=str))

    # Summary
    from collections import Counter
    spec = Counter(c["specificity"] for c in all_results)
    sent = Counter(c["sentiment"] for c in all_results)
    tier = Counter(c["quality_tier"] for c in all_results)
    print(f"\nDone. Specificity: {dict(spec)} | Sentiment: {dict(sent)} | Tier: {dict(tier)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

"""Step 4: assign L1/L2/L3 from approved taxonomy to each cluster."""
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
from l2.taxonomy import load_taxonomy, build_lookups, taxonomy_for_prompt, is_valid_combo, nearest_valid

MODEL = "gpt-4o"
WORKERS = 10
BATCH_SIZE = 20

IN_PATH = Path(__file__).parent / "data" / "scored_clusters.json"
OUT_PATH = Path(__file__).parent / "data" / "labelled_clusters.json"

SYSTEM = (
    "You are an attraction intelligence labelling engine. "
    "Assign exactly one valid (L1, L2, L3) triple from the APPROVED taxonomy to each cluster. "
    "Output strict JSON only."
)

# v2: famous-anchor overrides — these are signature pieces, never "Hidden Highlight".
FAMOUS_ANCHORS = {
    "Louvre Museum": {
        "mona lisa": ("Iconic Artwork", "Painting"),
        "la joconde": ("Iconic Artwork", "Painting"),
        "venus de milo": ("Iconic Artwork", "Sculpture"),
        "winged victory": ("Iconic Artwork", "Sculpture"),
        "winged victory of samothrace": ("Iconic Artwork", "Sculpture"),
        "liberty leading the people": ("Iconic Artwork", "Painting"),
        "louvre pyramid": ("Iconic Landmark", "Monument"),
        "the pyramid": ("Iconic Landmark", "Monument"),
    },
    "Colosseum": {
        "colosseum": ("Iconic Landmark", "Monument"),
        "arch of constantine": ("Iconic Landmark", "Monument"),
    },
}


def _apply_famous_anchor_override(c: dict, l1: str, l2: str, l3: str, lookups: dict) -> tuple[str, str, str, bool]:
    """If cluster has a famous anchor AND L2='Hidden Highlight', force to Iconic Artwork / Iconic Landmark."""
    if l2 != "Hidden Highlight":
        return l1, l2, l3, False
    poi = c.get("poi_name", "")
    anchors = [str(a).strip().lower() for a in (c.get("anchor_entities") or [])]
    # also check cluster name as a fallback
    name_l = (c.get("name") or "").lower()
    overrides = FAMOUS_ANCHORS.get(poi, {})
    target = None
    for key, repl in overrides.items():
        if key in anchors or key in name_l:
            target = repl
            break
    if not target:
        return l1, l2, l3, False
    new_l2, new_l3 = target
    new_l1 = "Attention Intelligence"
    if is_valid_combo(new_l1, new_l2, new_l3, lookups):
        return new_l1, new_l2, new_l3, True
    rec = nearest_valid(new_l1, new_l2, new_l3, lookups)
    if rec:
        return rec[0], rec[1], rec[2], True
    return l1, l2, l3, False

USER_TEMPLATE = """L1 = What traveler problem does this solve?
L2 = What kind of recommendation is this?
L3 = What thing is being recommended?

APPROVED TAXONOMY (only use these exact strings):
{taxonomy}

CLUSTERS TO LABEL:
{clusters_json}

For each cluster, choose the (L1, L2, L3) triple that BEST fits. If the cluster genuinely spans two L1s, set secondary_l1.

Return JSON:
{{
  "labels": [
    {{
      "cluster_id": <int>,
      "l1": "<L1 exactly as listed>",
      "l2": "<L2 exactly as listed under that L1>",
      "l3": "<L3 exactly as listed under that L1/L2>",
      "secondary_l1": "<another L1 if cluster genuinely spans, else null>",
      "confidence": 0.0,
      "reasoning": "<one short phrase>"
    }}
  ]
}}

RULES:
- L1, L2, L3 must EXACTLY match a triple in the approved taxonomy.
- Do NOT invent new labels.
- "Visit Intelligence" is for HOW-to-visit advice (route, time, crowd strategy). Don't use it as a catchall.
- "Attention Intelligence" is for what to NOTICE inside the POI.
- "Discovery Intelligence" is for what to explore NEARBY.
- "Culinary Intelligence" is for food/drink recommendations.
- "Operational Intelligence" is for complaints, access issues, comfort/safety, pricing, policy, facilities, third-party operators.
- confidence: 0.9+ = clear fit; 0.7-0.9 = good fit with minor ambiguity; <0.7 = uncertain.

SECONDARY_L1 — FIRE IT OFTEN. A cluster gets a secondary_l1 whenever the observation has a clear operational/access shadow on top of its primary intent. Aim to fire secondary_l1 on ~10% or more of clusters.

Few-shot examples for when to fire secondary_l1:
- Cluster: "Café Marly inside the Louvre is overpriced and slow during peak hours."
  → primary l1=Culinary Intelligence, l2=Quick Stop, l3=Coffee Shop; secondary_l1=Operational Intelligence
- Cluster: "The Mona Lisa room is chaotically overcrowded and visitors are rushed past in seconds."
  → primary l1=Attention Intelligence, l2=Iconic Artwork, l3=Painting; secondary_l1=Operational Intelligence
- Cluster: "Roma Pass saves time at the Colosseum entry queue and bundles transport."
  → primary l1=Visit Intelligence, l2=Time Allocation, l3=Sub-Attraction; secondary_l1=Operational Intelligence

RULE (must apply): If the cluster's sentiment is negative AND its size >= 5 AND primary L1 is not 'Operational Intelligence', set secondary_l1 = 'Operational Intelligence'. This is non-negotiable.

SOURCE-MIX + SENTIMENT ROUTING:
- If sentiment is "negative" AND source_mix spans >= 3 platforms, strongly prefer Operational Intelligence as PRIMARY l1 (the complaint is corroborated across the public web).
- If sentiment is "negative" AND it concerns pricing/queues/policy/facilities, also prefer Operational Intelligence primary.

STORY BEHIND IT vs ICONIC ARTWORK:
- Use L2="Story Behind It" / "Artifact" ONLY when the cluster name explicitly references historical context, origin myth, backstory, or "the story of X".
- Visual description of an artwork or landmark → Iconic Artwork or Hidden Highlight, NOT Story Behind It.
- "The Mona Lisa is smaller than expected" → Iconic Artwork, not Story Behind It.
- "Hidden Highlight" is for genuinely under-the-radar items. NEVER use Hidden Highlight for world-famous signature pieces (Mona Lisa, Venus de Milo, Winged Victory, Liberty Leading the People, Louvre Pyramid, Arch of Constantine).
"""


def label_batch(batch: list[dict], taxonomy_str: str, lookups: dict) -> list[dict]:
    clusters_json = json.dumps(
        [{
            "cluster_id": c["cluster_id"],
            "poi_name": c.get("poi_name"),
            "name": c["name"],
            "size": c["size"],
            "named_entity_score": c.get("named_entity_score"),
            "actionability_score": c.get("actionability_score"),
            "specificity": c.get("specificity"),
            "sentiment": c.get("sentiment"),
            "polarity": c.get("polarity"),
            "anchor_entities": c.get("anchor_entities") or [],
            "source_mix": c.get("source_mix") or {},
            "n_sources": c.get("n_sources", 1),
        } for c in batch],
        indent=1,
    )
    user = USER_TEMPLATE.format(taxonomy=taxonomy_str, clusters_json=clusters_json)
    t0 = time.time()
    try:
        out = call_json(SYSTEM, user, model=MODEL, temperature=0.1, max_tokens=3000)
        labels = out.get("labels") or []
        by_id = {l["cluster_id"]: l for l in labels if isinstance(l, dict)}
        results = []
        for c in batch:
            l = by_id.get(c["cluster_id"]) or {}
            l1 = (l.get("l1") or "").strip()
            l2 = (l.get("l2") or "").strip()
            l3 = (l.get("l3") or "").strip()
            secondary = (l.get("secondary_l1") or None) or None
            confidence = float(l.get("confidence", 0.7) or 0.7)
            reasoning = l.get("reasoning", "") or ""

            valid = is_valid_combo(l1, l2, l3, lookups)
            if not valid:
                # try graceful recovery
                recovered = nearest_valid(l1, l2, l3, lookups)
                if recovered:
                    l1, l2, l3 = recovered
                    valid = True
                    reasoning = (reasoning + " [auto-recovered]").strip()
                    confidence = min(confidence, 0.5)

            # v2: famous-anchor override — never label Mona Lisa et al. as Hidden Highlight
            l1, l2, l3, overridden = _apply_famous_anchor_override(c, l1, l2, l3, lookups)
            if overridden:
                reasoning = (reasoning + " [famous-anchor-override]").strip()
                valid = is_valid_combo(l1, l2, l3, lookups)

            results.append({
                **c,
                "l1": l1, "l2": l2, "l3": l3,
                "secondary_l1": secondary,
                "label_confidence": confidence,
                "label_reasoning": reasoning,
                "label_valid": valid,
                "label_elapsed_s": round(time.time() - t0, 2),
            })
        return results
    except Exception as e:
        return [{**c, "l1": "", "l2": "", "l3": "", "label_confidence": 0.0, "label_error": str(e), "label_valid": False} for c in batch]


def main():
    print("=== Step 4: Label Clusters ===")
    combos = load_taxonomy()
    lookups = build_lookups(combos)
    tax_str = taxonomy_for_prompt(combos)
    print(f"Loaded {len(combos)} valid (L1,L2,L3) combos across {len(lookups['l1_set'])} L1s")

    clusters = json.loads(IN_PATH.read_text())
    print(f"Labelling {len(clusters)} clusters in batches of {BATCH_SIZE} with {WORKERS} workers...")

    batches = [clusters[i:i + BATCH_SIZE] for i in range(0, len(clusters), BATCH_SIZE)]
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
    print(f"\nValid combos: {valid}/{len(all_results)} | Low-confidence (<0.7): {low_conf}")
    print(f"L1 distribution: {dict(l1_counts)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

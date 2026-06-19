"""Step 1: extract up to 3 actionable insights per review.

Output JSON shape per review:
{
  "review_id": str,
  "poi_id": str, "poi_name": str, "city": str,
  "rating": int, "helpful_count": int,
  "insights": [
    {"text": str, "strength": float, "anchor_entity": str|null},
    ...
  ]   # may be empty (review discarded as low-value/off-topic)
}
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
from db.client import get_client

MODEL = "gpt-4o"
WORKERS = 10
OUT_PATH = Path(__file__).parent / "data" / "insights.json"
SAMPLES_PATH = Path(__file__).parent / "data" / "raw_reviews.json"

POI_TARGETS = {
    "Colosseum": 500,
    "Louvre Museum": 500,
}

# rating band → count (must sum to per-POI target)
RATING_DISTRIBUTION = {
    5: 200,
    4: 100,
    3: 50,
    2: 50,
    1: 100,
}


SYSTEM = (
    "You are an attraction intelligence extraction engine. "
    "Convert raw traveler text into 0-3 reusable, generalised, named-entity-preserving insights "
    "for a travel intelligence repository. Output strict JSON only."
)

USER_TEMPLATE = """POI: {poi_name}, {city}
RATING: {rating}/5
REVIEW:
\"\"\"{text}\"\"\"

Extract up to 3 distinct, actionable insights. Return JSON:

{{
  "insights": [
    {{
      "text": "<one-sentence generalised insight; 10-25 words; preserves named entities>",
      "strength": 0.0,         // 0.0-1.0 how actionable + specific this insight is
      "anchor_entity": "<the named place/object/dish/area this is about, or null>"
    }}
  ]
}}

RULES:
1. Extract DISTINCT insights only. If review only contains one observation, return one.
2. DISCARD (return empty list) when review is: generic praise ("amazing!"), pure complaint with no signal ("waste of time"), single emoji, off-topic, fake-review-style.
   DO NOT DISCARD if the insight contains a specific named entity (room number, artwork name, restaurant name, entrance name, sub-attraction). Lower the bar — even thin context around a named entity is valuable signal.
3. Generalise tone: "I loved X" → "X is praised" or "X is recommended" (NOT "X is FREQUENTLY praised" — frequency is decided downstream).
4. Preserve specificity: prefer named rooms, artworks, restaurants, viewpoints, sub-attractions over generic terms.
5. Standalone: each insight must make sense without reading the review.
6. No anecdotes, no first-person, no filler.
7. Don't invent details not in the review.

GOOD insight examples:
- "The House of Augustus is cited as an underrated highlight on Palatine Hill."
- "The Louvre rewards early-morning arrival before tour groups."
- "Roscioli is recommended as a worthwhile dining stop near the Vatican."
- "The Mona Lisa room is reported as overcrowded and rushed."

BAD insight examples (don't return these):
- "The visit was great." (generic)
- "I loved the food." (personal, generic)
- "Avoid the queues." (no specific advice on how)

DISCARD outputs that reduce to generic praise like:
- "The Colosseum is a must-see attraction in Rome"
- "The Louvre is a world-famous museum"
- "An unforgettable experience"
- "Highly recommended for tourists"
Return empty insight rather than emit these.

PRESERVE numeric anchors verbatim:
- Prices (€17 entry, $50 tour)
- Durations (allow 3 hours, queue 90 minutes)
- Opening windows (open weekends only, closed Tuesdays)
- Distances (10 min walk from Metro Pyramides)
"""


def fetch_reviews_for_poi(poi_name: str, target: int) -> list[dict]:
    db = get_client()
    poi = db.table("pois").select("id, name, city").eq("name", poi_name).execute().data
    if not poi:
        raise RuntimeError(f"POI not found: {poi_name}")
    poi_id, name, city = poi[0]["id"], poi[0]["name"], poi[0]["city"]

    out: list[dict] = []
    for rating, n in RATING_DISTRIBUTION.items():
        rows = (
            db.table("tripadvisor_reviews")
            .select("id, review_id, rating, title, body, helpful_count, visit_date")
            .eq("poi_id", poi_id)
            .eq("rating", rating)
            .not_.is_("body", "null")
            .order("helpful_count", desc=True)
            .limit(n + 20)  # buffer for body-len filter
            .execute()
            .data
        ) or []
        kept = 0
        for r in rows:
            body = (r.get("body") or "").strip()
            if len(body) < 40:
                continue
            text = (r.get("title") or "").strip()
            full = f"{text}. {body}" if text else body
            out.append({
                "review_id": r["review_id"],
                "review_uuid": r["id"],
                "poi_id": poi_id,
                "poi_name": name,
                "city": city,
                "rating": r.get("rating"),
                "helpful_count": r.get("helpful_count") or 0,
                "visit_date": r.get("visit_date"),
                "text": full[:3500],  # cap at ~900 tokens
            })
            kept += 1
            if kept >= n:
                break
    return out


def extract_one(review: dict) -> dict:
    user = USER_TEMPLATE.format(
        poi_name=review["poi_name"],
        city=review["city"],
        rating=review["rating"],
        text=review["text"],
    )
    t0 = time.time()
    try:
        out = call_json(SYSTEM, user, model=MODEL, temperature=0.2, max_tokens=600)
        insights = out.get("insights") or []
        # Sanitise — must be a list of dicts with at least `text`
        clean = []
        for ins in insights[:3]:
            if not isinstance(ins, dict) or not ins.get("text"):
                continue
            clean.append({
                "text": str(ins["text"]).strip(),
                "strength": float(ins.get("strength", 0.5) or 0.5),
                "anchor_entity": (ins.get("anchor_entity") or None) or None,
            })
        return {
            "review_id": review["review_id"],
            "review_uuid": review["review_uuid"],
            "poi_id": review["poi_id"],
            "poi_name": review["poi_name"],
            "rating": review["rating"],
            "helpful_count": review["helpful_count"],
            "insights": clean,
            "elapsed_s": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {
            "review_id": review["review_id"],
            "review_uuid": review["review_uuid"],
            "poi_id": review["poi_id"],
            "poi_name": review["poi_name"],
            "rating": review["rating"],
            "helpful_count": review["helpful_count"],
            "insights": [],
            "error": str(e),
            "elapsed_s": round(time.time() - t0, 2),
        }


def main():
    print("=== Step 1: Insight Extraction ===")
    if SAMPLES_PATH.exists():
        print(f"Loading cached samples from {SAMPLES_PATH}")
        reviews = json.loads(SAMPLES_PATH.read_text())
    else:
        all_reviews: list[dict] = []
        for poi_name, target in POI_TARGETS.items():
            print(f"  Fetching up to {target} reviews for {poi_name}...")
            rs = fetch_reviews_for_poi(poi_name, target)
            print(f"    got {len(rs)} reviews")
            all_reviews.extend(rs)
        SAMPLES_PATH.write_text(json.dumps(all_reviews, indent=2, default=str))
        print(f"  Saved samples to {SAMPLES_PATH}")
        reviews = all_reviews

    print(f"\nTotal reviews to extract: {len(reviews)}")
    print(f"Calling {MODEL} with {WORKERS} parallel workers...")

    results: list[dict] = []
    n_insights = 0
    n_discard = 0
    n_error = 0
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(extract_one, r): r for r in reviews}
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            results.append(res)
            if res.get("error"):
                n_error += 1
            elif not res["insights"]:
                n_discard += 1
            else:
                n_insights += len(res["insights"])
            if i % 50 == 0 or i == len(reviews):
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                print(f"  [{i}/{len(reviews)}] insights so far: {n_insights} | discards: {n_discard} | errors: {n_error} | {rate:.1f} rev/s")

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nDone. {n_insights} insights from {len(reviews)} reviews ({n_discard} discarded, {n_error} errors).")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

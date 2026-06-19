"""Multi-source insight extraction: Google reviews, YouTube comments,
YouTube transcript chunks, Reddit posts, Reddit comments.

500 rows per (POI, source) — total ~5000 rows.
Outputs per-source files: l2/data/insights_<source>.json

Source-aware prompt preamble; otherwise same extraction rules as TripAdvisor pipeline.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from l2.fetchers import (
    fetch_google_reviews,
    fetch_youtube_comments,
    fetch_youtube_transcript_chunks,
    fetch_reddit_posts,
    fetch_reddit_comments,
)
from l2.llm_client import call_json

MODEL = "gpt-4o"
WORKERS = 80

POIS = {
    "Colosseum": ("1a20c26c-e5fd-4ef7-8afd-9d90a7ccf97a", "Rome"),
    "Louvre Museum": ("ea32b51d-31b5-47c6-a359-fe3652eab62e", "Paris"),
}

PER_POI_PER_SOURCE = 500

def _load_file_backed(filename: str):
    """Build a fetcher closure that loads rows from a JSON file and filters by poi_name."""
    def _fetch(poi_id, poi_name, city, limit):
        path = Path(__file__).parent / "data" / filename
        if not path.exists():
            print(f"  [warn] {path} missing — returning []")
            return []
        rows = json.loads(path.read_text())
        filtered = [r for r in rows if r.get("poi_name") == poi_name]
        return filtered[:limit]
    return _fetch


fetch_firecrawl_blog = _load_file_backed("raw_blog_culinary_clean.json")
fetch_reddit_targeted = _load_file_backed("raw_reddit_targeted_clean.json")


FETCHERS = {
    "google_review": fetch_google_reviews,
    "youtube_comment": fetch_youtube_comments,
    "youtube_transcript_chunk": fetch_youtube_transcript_chunks,
    "reddit_post": fetch_reddit_posts,
    "reddit_comment": fetch_reddit_comments,
    "firecrawl_blog": fetch_firecrawl_blog,
    "reddit_targeted": fetch_reddit_targeted,
}

# v2: drop youtube_comment entirely (low signal, off-topic fan-talk).
del FETCHERS["youtube_comment"]

SOURCE_PREAMBLE = {
    "google_review": "This is a Google Maps review (short, rating-anchored).",
    "youtube_comment": "This is a YouTube comment (short, reactive). Be especially strict about discarding off-topic, fan-talk, or generic content.",
    "youtube_transcript_chunk": "This is a 60-90 second chunk from a YouTube video transcript. The speaker may be describing what they see, giving advice, or providing historical context. Extract observations and recommendations from the narration, not the speaker's personal reactions.",
    "reddit_post": "This is a Reddit post — could be a trip-planning question, trip report, or recommendation thread. Extract advice/observations. If the post is purely a question with no recommendation, return empty.",
    "reddit_comment": "This is a Reddit comment replying to a post. Focus on the advice or observation the commenter is giving.",
    "firecrawl_blog": "This is a curated travel-blog excerpt about restaurants/food near {poi_name}. Extract concrete venue names, addresses, price hints, and food types. DISCARD if it's navigation/menu/footer cruft.",
    "reddit_targeted": "This is a Reddit post or comment by an actual traveler. Extract concrete tips, warnings, scams, specific recommendations. DISCARD generic karma-farming or off-topic content.",
}

SYSTEM = (
    "You are an attraction intelligence extraction engine. "
    "Convert raw traveler text into 0-3 reusable, generalised, named-entity-preserving insights "
    "for a travel intelligence repository. Output strict JSON only."
)

USER_TEMPLATE = """POI: {poi_name}, {city}
SOURCE NOTE: {source_note}

CONTENT:
\"\"\"{text}\"\"\"

Extract up to 3 distinct, actionable insights. Return JSON:

{{
  "insights": [
    {{
      "text": "<one-sentence generalised insight; 10-25 words; preserves named entities>",
      "strength": 0.0,
      "anchor_entity": "<named place/object/dish/area or null>"
    }}
  ]
}}

RULES:
1. Extract DISTINCT insights only.
2. DISCARD (return empty list) for: generic praise ("amazing!"), pure complaints with no signal, single emoji, off-topic, no actionable observation.
   DO NOT DISCARD if the insight contains a specific named entity (room number, artwork name, restaurant name, entrance name, sub-attraction). Lower the bar — even thin context around a named entity is valuable signal.
3. Generalise tone: "I loved X" → "X is praised". DO NOT use "frequently" — frequency comes downstream from clustering.
4. Preserve specificity: prefer named rooms, artworks, restaurants, viewpoints, sub-attractions over generic terms.
5. Standalone: each insight must make sense without reading the original.
6. No anecdotes, no first-person, no filler.
7. Don't invent details not present.

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


def extract_one(row: dict) -> dict:
    user = USER_TEMPLATE.format(
        poi_name=row["poi_name"],
        city=row["city"],
        source_note=SOURCE_PREAMBLE.get(row["source"], ""),
        text=row["text"],
    )
    t0 = time.time()
    try:
        out = call_json(SYSTEM, user, model=MODEL, temperature=0.2, max_tokens=600)
        insights = out.get("insights") or []
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
            "source": row["source"],
            "source_id": row["source_id"],
            "source_uuid": row["source_uuid"],
            "poi_id": row["poi_id"],
            "poi_name": row["poi_name"],
            "rating": row.get("rating"),
            "meta": row.get("meta", {}),
            "insights": clean,
            "elapsed_s": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {
            "source": row["source"],
            "source_id": row["source_id"],
            "source_uuid": row["source_uuid"],
            "poi_id": row["poi_id"],
            "poi_name": row["poi_name"],
            "rating": row.get("rating"),
            "meta": row.get("meta", {}),
            "insights": [],
            "error": str(e),
            "elapsed_s": round(time.time() - t0, 2),
        }


def fetch_all_for_source(source: str) -> list[dict]:
    fetcher = FETCHERS[source]
    out: list[dict] = []
    for poi_name, (poi_id, city) in POIS.items():
        rows = fetcher(poi_id, poi_name, city, PER_POI_PER_SOURCE)
        print(f"  fetched {source} | {poi_name}: {len(rows)} rows")
        out.extend(rows)
    return out


def extract_for_source(source: str) -> dict:
    print(f"\n=== {source.upper()} ===")
    rows = fetch_all_for_source(source)
    if not rows:
        print(f"  [{source}] no rows to extract — skipping")
        return {"source": source, "n_rows": 0}

    raw_path = Path(__file__).parent / "data" / f"raw_{source}.json"
    raw_path.write_text(json.dumps(rows, indent=2, default=str))

    results: list[dict] = []
    n_insights = n_discard = n_err = 0
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(extract_one, r): r for r in rows}
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            results.append(res)
            if res.get("error"):
                n_err += 1
            elif not res["insights"]:
                n_discard += 1
            else:
                n_insights += len(res["insights"])
            if i % 50 == 0 or i == len(rows):
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                print(f"  [{source}] [{i}/{len(rows)}] insights={n_insights} discards={n_discard} errors={n_err} {rate:.1f} rev/s")

    out_path = Path(__file__).parent / "data" / f"insights_{source}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"  [{source}] Done. {n_insights} insights from {len(rows)} rows ({n_discard} discarded, {n_err} errors). Wrote {out_path}")
    return {"source": source, "n_rows": len(rows), "n_insights": n_insights, "n_discard": n_discard, "n_err": n_err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(FETCHERS.keys()) + ["all"], default="all")
    args = ap.parse_args()

    sources = [args.source] if args.source != "all" else list(FETCHERS.keys())
    summary = []
    for s in sources:
        summary.append(extract_for_source(s))

    print("\n=== SUMMARY ===")
    for r in summary:
        print(f"  {r}")


if __name__ == "__main__":
    main()

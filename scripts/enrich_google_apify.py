"""Enrich google_reviews with Apify compass/Google-Maps-Reviews-Scraper.

Targets Colosseum and Louvre only. Runs three sort passes per POI:
  mostRelevant   → up to 2,000 reviews
  highestRanking → up to 1,000 reviews
  lowestRanking  → up to 1,000 reviews

Budget: $5 total, split evenly across both POIs ($2.50 each).
Reviews shorter than 75 chars are dropped before upsert.
Idempotent: re-running is safe (UPSERT on review_id).

Usage:
  python -u scripts/enrich_google_apify.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.client import get_client
from scrapers.google_reviews_apify import scrape_poi_google_apify

TARGET_POI_NAMES = {"Colosseum", "Louvre Museum"}

# Full config — mostRelevant already done for Colosseum on first run.
SORT_CONFIGS_FULL = [
    ("mostRelevant",   2000),
    ("highestRanking", 1000),
    ("lowestRanking",  1000),
]

SORT_CONFIGS_REMAINING = [
    ("highestRanking", 1000),
    ("lowestRanking",  1000),
]

# Colosseum mostRelevant completed (1961 saved). Resume from highestRanking.
POI_SORT_CONFIGS: dict[str, list] = {
    "Colosseum":    SORT_CONFIGS_REMAINING,
    "Louvre Museum": SORT_CONFIGS_FULL,
}

TOTAL_BUDGET_USD = 5.0
BUDGET_PER_POI   = TOTAL_BUDGET_USD / len(TARGET_POI_NAMES)  # $2.50 each


def main() -> int:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("ERROR: APIFY_TOKEN not set", file=sys.stderr)
        return 1

    db = get_client()
    all_pois = db.table("pois").select("id,name,place_id").execute().data or []
    targets = [p for p in all_pois if p["name"] in TARGET_POI_NAMES and p.get("place_id")]

    missing = TARGET_POI_NAMES - {p["name"] for p in targets}
    if missing:
        print(f"WARNING: POIs not found or missing place_id: {missing}", file=sys.stderr)

    if not targets:
        print("No target POIs found — nothing to do.")
        return 0

    grand_total_saved = 0
    grand_total_skipped = 0

    for poi in targets:
        print(f"\n{'='*60}")
        print(f"POI: {poi['name']}  (place_id={poi['place_id']})")
        poi_sorts = POI_SORT_CONFIGS.get(poi["name"], SORT_CONFIGS_FULL)
        print(f"Budget: ${BUDGET_PER_POI:.2f}  |  sorts: {[s for s, _ in poi_sorts]}")
        print(f"{'='*60}")

        result = scrape_poi_google_apify(
            poi,
            sort_configs=POI_SORT_CONFIGS.get(poi["name"], SORT_CONFIGS_FULL),
            apify_token=token,
            max_total_charge_usd=BUDGET_PER_POI,
        )
        grand_total_saved   += result["saved"]
        grand_total_skipped += result["skipped"]

        print(f"\n  {poi['name']} summary:")
        for sr in result["sort_results"]:
            print(f"    {sr['sort_mode']:<18} fetched={sr['fetched']:>5}  saved={sr['saved']:>5}  skipped_short={sr['skipped']:>4}")

    print(f"\n{'='*60}")
    print(f"Run complete: {grand_total_saved} reviews saved, {grand_total_skipped} dropped (too short).")
    print(f"Re-run is safe — UPSERT on review_id.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

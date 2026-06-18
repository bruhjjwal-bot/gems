import os
from dotenv import load_dotenv
load_dotenv()

from db.client import get_client
from scrapers.google_reviews import scrape_poi_google_reviews
from scrapers.youtube import scrape_poi_youtube


def get_all_pois() -> list[dict]:
    db = get_client()
    return db.table("pois").select("id, name, place_id").execute().data or []


def run_youtube(pois: list[dict]):
    for poi in pois:
        scrape_poi_youtube(poi_id=poi["id"], poi_name=poi["name"])


def run_google_reviews(pois: list[dict]):
    sort_modes = tuple(
        s.strip() for s in os.environ.get(
            "GOOGLE_SORT_MODES", "newest,lowest,highest"
        ).split(",")
        if s.strip()
    )
    calls_per_stream = int(os.environ.get("CALLS_PER_STREAM", "1"))
    pause = float(os.environ.get("PAUSE_SECONDS", "80"))

    print(
        f"Run config: sorts={sort_modes}, calls_per_stream={calls_per_stream}, "
        f"pause={pause}s between calls"
    )
    total_calls = 0
    total_saved = 0
    for poi in pois:
        if not poi.get("place_id"):
            print(f"\nSkipping {poi['name']}: no place_id")
            continue
        print(f"\n=== {poi['name']} ===")
        result = scrape_poi_google_reviews(
            poi["id"],
            poi["place_id"],
            sort_modes=sort_modes,
            calls_per_stream=calls_per_stream,
            page_pause_seconds=pause,
        )
        total_calls += result["calls"]
        total_saved += result["saved"]

    print(
        f"\nRun summary: {total_calls} SerpApi calls, {total_saved} review rows upserted. "
        f"Re-run to continue from saved cursors."
    )


def main():
    pois = get_all_pois()
    if not pois:
        print("No POIs found. Insert rows into the `pois` table first.")
        return

    # YouTube already scraped — uncomment to re-run
    # run_youtube(pois)

    run_google_reviews(pois)


if __name__ == "__main__":
    main()

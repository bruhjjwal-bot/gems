"""Phase 1 live scrape — Louvre only, $13 hard cap via Apify."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.client import get_client
from scrapers.reddit import scrape_poi_reddit


def main():
    db = get_client()
    rows = db.table("pois").select("id, name, aliases").eq("name", "Louvre Museum").execute().data
    louvre = rows[0]
    poi_id = louvre["id"]
    print(f"Louvre id={poi_id}, aliases={louvre['aliases']}")

    # Purge prior Louvre reddit data so the run starts clean.
    db.table("poi_reddit_links").delete().eq("poi_id", poi_id).execute()
    post_rows = db.table("reddit_posts").select("id").eq("poi_id", poi_id).execute().data or []
    print(f"Purging {len(post_rows)} prior posts + their comments")
    for p in post_rows:
        db.table("reddit_comments").delete().eq("post_id", p["id"]).execute()
    db.table("reddit_posts").delete().eq("poi_id", poi_id).execute()

    result = scrape_poi_reddit(
        louvre,
        max_templates=30,
        max_urls_per_template=8,
        max_comments_per_post=30,
        max_total_charge_usd=13.0,
    )
    print(f"\n=== DONE ===\n{result}")


if __name__ == "__main__":
    main()

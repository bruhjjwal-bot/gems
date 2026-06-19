"""Phase 1 Colosseum scrape — full 60-template coverage, $10 cap."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.client import get_client
from scrapers.reddit import scrape_poi_reddit


def main():
    db = get_client()
    poi = db.table("pois").select("id, name, aliases").eq("name", "Colosseum").execute().data[0]
    poi_id = poi["id"]
    print(f"Colosseum id={poi_id}, aliases={poi['aliases']}")

    # Purge any prior reddit data for Colosseum.
    db.table("poi_reddit_links").delete().eq("poi_id", poi_id).execute()
    prior = db.table("reddit_posts").select("id").eq("poi_id", poi_id).execute().data or []
    for p in prior:
        db.table("reddit_comments").delete().eq("post_id", p["id"]).execute()
    db.table("reddit_posts").delete().eq("poi_id", poi_id).execute()
    print(f"Purged {len(prior)} prior posts.")

    result = scrape_poi_reddit(
        poi,
        max_templates=60,
        max_urls_per_template=8,
        max_comments_per_post=30,
        max_total_charge_usd=10.0,
    )
    print(f"\n=== DONE ===\n{result}")


if __name__ == "__main__":
    main()

"""Phase 1b: re-run Louvre with full template coverage (60 templates incl. all
13 intent categories), skipping URLs already enriched. Caps remaining budget
at ~$10 after the $2.93 burned by Phase 1a."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.client import get_client
from scrapers.reddit import scrape_poi_reddit


def main():
    db = get_client()
    louvre = db.table("pois").select("id, name, aliases").eq("name", "Louvre Museum").execute().data[0]
    print(f"Louvre id={louvre['id']}, aliases={louvre['aliases']}")
    # NO purge — we want to keep the 65 posts/1428 comments already saved.
    result = scrape_poi_reddit(
        louvre,
        max_templates=60,             # cover all template×alias expansions
        max_urls_per_template=8,
        max_comments_per_post=30,
        max_total_charge_usd=10.0,    # remaining budget headroom
        skip_existing_urls=True,
    )
    print(f"\n=== DONE ===\n{result}")


if __name__ == "__main__":
    main()

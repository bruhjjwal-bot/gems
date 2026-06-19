"""One-off: add 20 more videos for Colosseum using broader search templates."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from scrapers.youtube import scrape_additional_videos_for_poi

COLOSSEUM_POI_ID = "1a20c26c-e5fd-4ef7-8afd-9d90a7ccf97a"

if __name__ == "__main__":
    scrape_additional_videos_for_poi(
        poi_id=COLOSSEUM_POI_ID,
        poi_name="Colosseum",
        target_new=20,
    )

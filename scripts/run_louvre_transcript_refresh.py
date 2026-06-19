"""One-off: refresh missing transcripts for Louvre Museum."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from scrapers.youtube import refresh_transcripts_for_poi

LOUVRE_POI_ID = "ea32b51d-31b5-47c6-a359-fe3652eab62e"

if __name__ == "__main__":
    refresh_transcripts_for_poi(poi_id=LOUVRE_POI_ID, poi_name="Louvre Museum")

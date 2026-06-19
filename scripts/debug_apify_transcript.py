"""Debug: inspect raw Apify output for one video."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from apify_client import ApifyClient
from datetime import timedelta

client = ApifyClient(os.environ["APIFY_TOKEN"])
# "How the Colosseum Actually Worked" — a real video we know exists
VIDEO_URL = "https://www.youtube.com/watch?v=PNjuSMSy9MU"  # "How To Visit the Louvre Quickly" — known to have transcript

run = client.actor("pintostudio/youtube-transcript-scraper").call(
    run_input={"videoUrl": VIDEO_URL, "targetLanguage": "en"},
    run_timeout=timedelta(minutes=2),
)
print("run keys:", dir(run) if run else "None")
print("run type:", type(run))
print("run repr:", repr(run)[:500])

dataset_id = getattr(run, "default_dataset_id", None) or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
print("dataset_id:", dataset_id)

items = list(client.dataset(dataset_id).iterate_items())
print(f"\nTotal items: {len(items)}")
if items:
    print("First 2 items:")
    for item in items[:2]:
        print(json.dumps(item, indent=2))

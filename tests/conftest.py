import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from dotenv import load_dotenv

load_dotenv()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: marks tests that hit real external services (FireCrawl, Supabase live calls)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m"):
        return
    skip_live = pytest.mark.skip(reason="live test; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def test_poi():
    """Create a disposable POI row for tests that need a real foreign key.

    Cleans up the POI and all its google_reviews on teardown.
    """
    from db.client import get_client

    db = get_client()
    name = "__TEST_POI__"
    # Ensure clean slate
    existing = db.table("pois").select("id").eq("name", name).execute()
    for row in existing.data or []:
        db.table("google_reviews_cursors").delete().eq("poi_id", row["id"]).execute()
        db.table("google_reviews").delete().eq("poi_id", row["id"]).execute()
        db.table("pois").delete().eq("id", row["id"]).execute()

    inserted = (
        db.table("pois")
        .insert({"name": name, "city": "Testville", "country": "Testland"})
        .execute()
    )
    poi_id = inserted.data[0]["id"]
    yield poi_id

    db.table("google_reviews_cursors").delete().eq("poi_id", poi_id).execute()
    db.table("google_reviews").delete().eq("poi_id", poi_id).execute()
    db.table("pois").delete().eq("id", poi_id).execute()

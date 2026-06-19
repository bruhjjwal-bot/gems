import json
from pathlib import Path

import pytest

from db.client import get_client
from scrapers.google_reviews_apify import parse_apify_items, save_apify_reviews, fetch_apify_reviews, scrape_poi_google_apify

FIXTURES = Path(__file__).parent / "fixtures" / "apify"


def test_parse_apify_items_normalizes_sort_mode():
    long_text = "A" * 80
    items = [{"reviewId": "x1", "name": "A", "stars": 5, "text": long_text, "likesCount": 0}]
    assert parse_apify_items(items, sort_mode="mostRelevant")[0]["sort_mode"] == "most_relevant"
    assert parse_apify_items(items, sort_mode="highestRanking")[0]["sort_mode"] == "highest"
    assert parse_apify_items(items, sort_mode="lowestRanking")[0]["sort_mode"] == "lowest"
    assert parse_apify_items(items, sort_mode="newest")[0]["sort_mode"] == "newest"


def test_parse_apify_items_drops_short_reviews():
    items = json.loads((FIXTURES / "google_reviews_sample.json").read_text())
    reviews = parse_apify_items(items, sort_mode="mostRelevant")
    texts = [r["text"] for r in reviews]
    # "Ok" (2 chars) must be absent; both long reviews must survive
    assert all(len(t) >= 75 for t in texts)
    assert len(reviews) == 2


def test_parse_apify_items_maps_all_fields():
    items = json.loads((FIXTURES / "google_reviews_sample.json").read_text())
    reviews = parse_apify_items(items, sort_mode="mostRelevant")

    # third item ("Ok") is under 75 chars and should be dropped
    assert len(reviews) == 2

    first = reviews[0]
    assert first["review_id"] == "ChZDSUhNMG9nS0VJQ0FnSURmMTZxd1JBEAE"
    assert first["author"] == "Maria Rossi"
    assert first["rating"] == 5
    assert "breathtaking" in first["text"]
    assert first["iso_date"] == "2026-05-15T08:22:00.000Z"
    assert first["likes"] == 12
    assert first["is_local_guide"] is True
    assert first["reviewer_review_count"] == 47
    assert first["owner_response"] == "Thank you for your kind words, Maria!"
    assert first["review_url"] == "https://www.google.com/maps/reviews/data=abc123"
    assert first["original_language"] == "en"
    assert first["sort_mode"] == "most_relevant"


def test_save_apify_reviews_is_idempotent(test_poi):
    long_text = "This is a detailed review that exceeds seventy-five characters easily for testing."
    reviews = parse_apify_items([
        {"reviewId": "apify_save_1", "name": "Alice", "stars": 5, "text": long_text, "likesCount": 2,
         "isLocalGuide": True, "reviewerNumberOfReviews": 10},
        {"reviewId": "apify_save_2", "name": "Bob", "stars": 3, "text": long_text, "likesCount": 0,
         "isLocalGuide": False, "reviewerNumberOfReviews": 3},
    ], sort_mode="mostRelevant")

    save_apify_reviews(test_poi, reviews, sort_mode="most_relevant")
    save_apify_reviews(test_poi, reviews, sort_mode="most_relevant")  # second call same data

    db = get_client()
    rows = db.table("google_reviews").select("review_id,is_local_guide,reviewer_review_count").eq("poi_id", test_poi).execute().data
    assert len(rows) == 2
    assert {r["review_id"] for r in rows} == {"apify_save_1", "apify_save_2"}
    local_guide_row = next(r for r in rows if r["review_id"] == "apify_save_1")
    assert local_guide_row["is_local_guide"] is True
    assert local_guide_row["reviewer_review_count"] == 10


@pytest.mark.live
def test_smoke_fetch_colosseum_most_relevant(test_poi):
    """Fetch 5 reviews for Colosseum via Apify, verify they land in DB."""
    import os
    token = os.environ["APIFY_TOKEN"]
    colosseum_place_id = "ChIJrTLr-GyuEmsRBfy61i59si0"

    raw_items = fetch_apify_reviews(
        colosseum_place_id,
        sort_mode="mostRelevant",
        max_reviews=5,
        apify_token=token,
        max_total_charge_usd=1.0,
    )
    assert len(raw_items) >= 1, "Apify returned no items"

    reviews = parse_apify_items(raw_items, sort_mode="mostRelevant")
    assert len(reviews) >= 1, "all reviews were under 75 chars — unexpected"

    saved = save_apify_reviews(test_poi, reviews, sort_mode="most_relevant")
    assert saved == len(reviews)

    db = get_client()
    rows = db.table("google_reviews").select("review_id,is_local_guide,sort_mode").eq("poi_id", test_poi).execute().data
    assert len(rows) == saved
    assert all(r["sort_mode"] == "most_relevant" for r in rows)

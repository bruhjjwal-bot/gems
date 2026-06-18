import json
from pathlib import Path

import pytest

from db.client import get_client
from scrapers.google_reviews import (
    build_maps_url,
    dedupe_reviews,
    fetch_reviews,
    get_cursor,
    parse_reviews,
    save_cursor,
    save_reviews,
    scrape_poi_google_reviews,
)

FIXTURES = Path(__file__).parent / "fixtures" / "serpapi"


def test_build_maps_url_contains_place_id():
    place_id = "ChIJLU7jZClu5kcR4PcOOO6p3I0"  # Eiffel Tower
    url = build_maps_url(place_id)
    assert place_id in url
    assert "google.com/maps" in url


def test_parse_reviews_extracts_all_fields():
    sample = json.loads((FIXTURES / "sample_response.json").read_text())
    reviews = parse_reviews(sample)

    # SerpApi returns 8 reviews on the first page
    assert len(reviews) == 8

    first = reviews[0]
    assert first["review_id"] == "Ci9DQUlRQUNvZENodHljRjlvT2pBM1dUQmtPRGcyYW0xdFltNDRhMWt3WDJRelRYYxAB"
    assert first["author"] == "Nathan"
    assert first["rating"] == 5  # rating comes as 5.0 float from SerpApi; we normalize to int
    assert "Trevi Fountain" in first["text"]
    assert first["relative_time"] == "2 weeks ago"
    assert first["iso_date"] == "2026-06-03T16:40:18Z"
    assert first["likes"] == 0


def test_dedupe_reviews_collapses_duplicates():
    reviews = [
        {"review_id": "a", "author": "Alice", "rating": 5, "text": "first", "relative_time": "now", "likes": 0},
        {"review_id": "b", "author": "Bob", "rating": 3, "text": "x", "relative_time": "now", "likes": 0},
        {"review_id": "a", "author": "Alice", "rating": 5, "text": "second copy", "relative_time": "now", "likes": 0},
        {"review_id": "c", "author": "Carla", "rating": 4, "text": "y", "relative_time": "now", "likes": 0},
        {"review_id": "b", "author": "Bob", "rating": 3, "text": "x", "relative_time": "now", "likes": 0},
    ]
    deduped = dedupe_reviews(reviews)
    ids = [r["review_id"] for r in deduped]
    assert ids == ["a", "b", "c"]
    # first occurrence wins
    assert deduped[0]["text"] == "first"


def test_save_reviews_is_idempotent(test_poi):
    reviews = [
        {"review_id": "save_test_1", "author": "A", "rating": 5, "text": "x", "relative_time": "now", "likes": 1},
        {"review_id": "save_test_2", "author": "B", "rating": 4, "text": "y", "relative_time": "now", "likes": 0},
        {"review_id": "save_test_3", "author": "C", "rating": 3, "text": "z", "relative_time": "now", "likes": 0},
    ]

    save_reviews(test_poi, reviews, sort_mode="most_relevant")
    save_reviews(test_poi, reviews, sort_mode="most_relevant")  # second call same data

    db = get_client()
    rows = db.table("google_reviews").select("review_id").eq("poi_id", test_poi).execute().data
    assert len(rows) == 3
    assert {r["review_id"] for r in rows} == {"save_test_1", "save_test_2", "save_test_3"}


def test_cursor_round_trip(test_poi):
    # initial: no cursor
    assert get_cursor(test_poi, "newest") == {"token": None, "exhausted": False}

    save_cursor(test_poi, "newest", token="token_abc", exhausted=False)
    assert get_cursor(test_poi, "newest") == {"token": "token_abc", "exhausted": False}

    # update to exhausted
    save_cursor(test_poi, "newest", token="token_xyz", exhausted=True)
    assert get_cursor(test_poi, "newest") == {"token": "token_xyz", "exhausted": True}

    # other sort_mode unaffected
    assert get_cursor(test_poi, "lowest") == {"token": None, "exhausted": False}


@pytest.mark.live
def test_fetch_reviews_live():
    """Hit SerpApi for real. Trevi Fountain, 3 calls (1 first-page + 2 paginated)."""
    place_id = "ChIJ1UCDJ1NgLxMRtrsCzOHxdvY"  # Trevi Fountain
    response = fetch_reviews(place_id, sort_mode="most_relevant", max_calls=3, page_pause_seconds=1)

    assert "reviews" in response
    assert "last_token" in response
    assert "exhausted" in response
    raw_reviews = response["reviews"]
    assert len(raw_reviews) >= 25, f"expected >=25 reviews from 3 SerpApi calls, got {len(raw_reviews)}"

    parsed = parse_reviews(response)
    first = parsed[0]
    assert first["review_id"]
    assert first["rating"] in {1, 2, 3, 4, 5, None}


@pytest.mark.live
def test_fetch_reviews_resumes_from_token():
    """A 1-call fetch returns last_token; passing it back yields a disjoint page."""
    place_id = "ChIJ1UCDJ1NgLxMRtrsCzOHxdvY"  # Trevi Fountain
    page1 = fetch_reviews(place_id, max_calls=1, page_pause_seconds=1)
    assert page1["last_token"], "expected a next_page_token after first call"
    assert not page1["exhausted"]
    page1_ids = {r["review_id"] for r in page1["reviews"]}
    assert page1_ids

    page2 = fetch_reviews(place_id, start_token=page1["last_token"], max_calls=1, page_pause_seconds=1)
    page2_ids = {r["review_id"] for r in page2["reviews"]}
    assert page2_ids, "resumed call returned no reviews"
    assert page1_ids.isdisjoint(page2_ids), "resumed call overlaps with first page"


@pytest.mark.live
def test_scrape_poi_google_reviews_uses_cursor(test_poi):
    """Two consecutive runs: second resumes from saved cursor, returns disjoint reviews."""
    place_id = "ChIJ1UCDJ1NgLxMRtrsCzOHxdvY"  # Trevi Fountain

    first = scrape_poi_google_reviews(
        test_poi, place_id,
        sort_modes=("most_relevant",),
        calls_per_stream=1,
        page_pause_seconds=1,
    )
    assert first["saved"] >= 1
    assert first["calls"] == 1

    cursor_after_first = get_cursor(test_poi, "most_relevant")
    assert cursor_after_first["token"] or cursor_after_first["exhausted"]

    db = get_client()
    ids_after_first = {
        r["review_id"]
        for r in db.table("google_reviews").select("review_id").eq("poi_id", test_poi).execute().data
    }

    # Second run picks up from saved cursor
    second = scrape_poi_google_reviews(
        test_poi, place_id,
        sort_modes=("most_relevant",),
        calls_per_stream=1,
        page_pause_seconds=1,
    )
    assert second["calls"] == 1

    ids_after_second = {
        r["review_id"]
        for r in db.table("google_reviews").select("review_id").eq("poi_id", test_poi).execute().data
    }
    new_ids = ids_after_second - ids_after_first
    assert new_ids, "second run should have added new reviews not in first run"

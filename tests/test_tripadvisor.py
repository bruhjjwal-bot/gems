import json
from pathlib import Path

import pytest

from db.client import get_client
from scrapers.tripadvisor import (
    _synthetic_review_id,
    build_tripadvisor_url,
    dedupe_reviews,
    fetch_reviews_page,
    get_cursor,
    parse_reviews,
    save_cursor,
    save_reviews,
    scrape_poi_tripadvisor,
)


FIXTURES = Path(__file__).parent / "fixtures" / "firecrawl"

TREVI_BASE = "https://www.tripadvisor.com/Attraction_Review-g187791-d190131-Reviews-Trevi_Fountain-Rome_Lazio.html"


def test_build_url_offset_zero_returns_base():
    assert build_tripadvisor_url(TREVI_BASE, 0) == TREVI_BASE


def test_build_url_offset_splices_or_segment():
    expected = "https://www.tripadvisor.com/Attraction_Review-g187791-d190131-Reviews-or10-Trevi_Fountain-Rome_Lazio.html"
    assert build_tripadvisor_url(TREVI_BASE, 10) == expected


def test_build_url_large_offset():
    expected = "https://www.tripadvisor.com/Attraction_Review-g187791-d190131-Reviews-or40390-Trevi_Fountain-Rome_Lazio.html"
    assert build_tripadvisor_url(TREVI_BASE, 40390) == expected


def test_build_url_rejects_negative_offset():
    with pytest.raises(ValueError):
        build_tripadvisor_url(TREVI_BASE, -1)


def test_build_url_rejects_malformed_base():
    with pytest.raises(ValueError):
        build_tripadvisor_url("https://www.tripadvisor.com/Foo.html", 10)


def test_parse_reviews_extracts_all_fields():
    """Against the captured Trevi Fountain smoke-test fixture, expect ~10 reviews
    with stable review_ids, authors, ratings, titles, visit dates, and bodies."""
    payload = json.loads((FIXTURES / "tripadvisor_trevi_smoketest.json").read_text())
    reviews = parse_reviews(payload)

    assert len(reviews) >= 8, f"expected at least 8 reviews on a default-page fetch, got {len(reviews)}"

    first = reviews[0]
    assert first["review_id"].startswith("r"), f"review_id should look like 'r12345', got {first['review_id']!r}"
    assert first["author"], "first review must have an author"
    assert first["rating"] in {1, 2, 3, 4, 5}, f"rating must be 1-5, got {first['rating']!r}"
    assert first["title"], "first review must have a title"
    assert first["body"], "first review must have body text"
    assert first["written_date"], "first review must have a written_date"
    # visit_date format like 'Jun 2026'
    assert first["visit_date"], "first review must have a visit_date"

    # All review_ids should be unique (no duplicates from parsing artifacts)
    ids = [r["review_id"] for r in reviews]
    assert len(ids) == len(set(ids)), f"duplicate review_ids in parse output: {ids}"


def test_parse_reviews_handles_missing_optional_fields():
    """A minimal review block with only required fields should still parse — missing
    trip_type, contributions, location should yield None rather than crash."""
    minimal_markdown = """[![](avatar.jpg)](https://www.tripadvisor.com/Profile/anon)

[Anonymous](https://www.tripadvisor.com/Profile/anon)

1 contribution

0

4 of 5 bubbles

### [Quick visit](https://www.tripadvisor.com/ShowUserReviews-g1-d2-r999999999-Slug-City.html)

Aug 2025

Short body of text describing the visit briefly.

Read more

Written September 1, 2025
"""
    reviews = parse_reviews({"markdown": minimal_markdown})
    assert len(reviews) == 1
    r = reviews[0]
    assert r["review_id"] == "r999999999"
    assert r["author"] == "Anonymous"
    assert r["rating"] == 4
    assert r["title"] == "Quick visit"
    assert r["visit_date"] == "Aug 2025"
    assert r["trip_type"] is None
    assert "Short body" in r["body"]


def test_parse_reviews_handles_variant3_no_permalink():
    """Eiffel offset=200 fixture is a captured variant-3 page where review
    permalinks are stripped. Parser must emit synthetic IDs and still extract
    the body, rating, title, dates."""
    payload = json.loads((FIXTURES / "tripadvisor_eiffel_offset200.json").read_text())
    reviews = parse_reviews(payload)

    assert len(reviews) >= 5, f"expected variant-3 parser to find at least 5 reviews, got {len(reviews)}"

    syn_count = sum(1 for r in reviews if r["review_id"].startswith("syn_"))
    assert syn_count >= 5, f"expected at least 5 reviews to use synthetic IDs, got {syn_count}"

    first = reviews[0]
    assert first["rating"] in {1, 2, 3, 4, 5}
    assert first["body"], "variant-3 review should still have body text"
    assert first["written_date"], "variant-3 review should still have a written_date"

    ids = [r["review_id"] for r in reviews]
    assert len(ids) == len(set(ids)), "synthetic IDs should be unique per review on this page"


def test_synthetic_review_id_is_deterministic():
    """Same inputs always produce the same synthetic_id; minor whitespace
    differences are normalized."""
    a = _synthetic_review_id("Alice", "Beautiful place.", "May 2026", "June 1, 2026")
    b = _synthetic_review_id("Alice", "Beautiful place.", "May 2026", "June 1, 2026")
    assert a == b
    # Different body → different id
    c = _synthetic_review_id("Alice", "Bad scam.", "May 2026", "June 1, 2026")
    assert a != c
    # Whitespace normalization
    d = _synthetic_review_id("  Alice  ", "Beautiful  place.\n", "May 2026", "June 1, 2026")
    assert a == d


def test_dedupe_reviews_collapses_duplicates():
    reviews = [
        {"review_id": "r1", "author": "A", "rating": 5, "title": "x", "body": "first", "visit_date": "Jun 2026", "trip_type": None, "written_date": None, "author_profile": None},
        {"review_id": "r2", "author": "B", "rating": 4, "title": "y", "body": "x", "visit_date": "Jun 2026", "trip_type": None, "written_date": None, "author_profile": None},
        {"review_id": "r1", "author": "A", "rating": 5, "title": "x", "body": "duplicate", "visit_date": "Jun 2026", "trip_type": None, "written_date": None, "author_profile": None},
        {"review_id": "r3", "author": "C", "rating": 3, "title": "z", "body": "third", "visit_date": "Jun 2026", "trip_type": None, "written_date": None, "author_profile": None},
    ]
    deduped = dedupe_reviews(reviews)
    assert [r["review_id"] for r in deduped] == ["r1", "r2", "r3"]
    assert deduped[0]["body"] == "first"  # first occurrence wins


def test_save_reviews_is_idempotent(test_poi):
    reviews = [
        {"review_id": "ta_save_test_1", "author": "Alice", "author_profile": "alice123", "rating": 5, "title": "Great", "body": "ok", "visit_date": "Jun 2026", "trip_type": "Couples", "written_date": "June 1, 2026"},
        {"review_id": "ta_save_test_2", "author": "Bob", "author_profile": "bob456", "rating": 3, "title": "Meh", "body": "fine", "visit_date": "May 2026", "trip_type": None, "written_date": "May 30, 2026"},
        {"review_id": "ta_save_test_3", "author": "Carla", "author_profile": "carla789", "rating": 1, "title": "Bad", "body": "scam", "visit_date": "Apr 2026", "trip_type": "Solo", "written_date": "April 15, 2026"},
    ]
    save_reviews(test_poi, reviews, sort_mode="most_recent")
    save_reviews(test_poi, reviews, sort_mode="most_recent")

    db = get_client()
    rows = db.table("tripadvisor_reviews").select("review_id, rating, trip_type").eq("poi_id", test_poi).execute().data
    assert len(rows) == 3
    assert {r["review_id"] for r in rows} == {"ta_save_test_1", "ta_save_test_2", "ta_save_test_3"}


def test_cursor_round_trip(test_poi):
    assert get_cursor(test_poi, "most_recent") == {"offset": 0, "exhausted": False}

    save_cursor(test_poi, "most_recent", offset=20, exhausted=False)
    assert get_cursor(test_poi, "most_recent") == {"offset": 20, "exhausted": False}

    save_cursor(test_poi, "most_recent", offset=50, exhausted=True)
    assert get_cursor(test_poi, "most_recent") == {"offset": 50, "exhausted": True}

    # other sort_mode unaffected
    assert get_cursor(test_poi, "lowest") == {"offset": 0, "exhausted": False}


@pytest.mark.live
def test_fetch_reviews_page_live():
    """One real FireCrawl call against Trevi Fountain's default page."""
    payload = fetch_reviews_page(TREVI_BASE, offset=0, throttle_seconds=1)
    assert payload.get("markdown"), "expected non-empty markdown in fetched page"
    parsed = parse_reviews(payload)
    assert len(parsed) >= 5, f"expected >=5 reviews on first page, got {len(parsed)}"


@pytest.mark.live
def test_scrape_poi_tripadvisor_uses_cursor(test_poi):
    """Two consecutive runs: second resumes from saved cursor."""
    db = get_client()
    # point our test POI at Trevi Fountain
    db.table("pois").update({"ta_url": TREVI_BASE}).eq("id", test_poi).execute()

    first = scrape_poi_tripadvisor(test_poi, TREVI_BASE, pages_per_run=1, throttle_seconds=1)
    assert first["saved"] >= 1
    assert first["pages"] == 1
    cursor_after_first = get_cursor(test_poi, "most_recent")
    assert cursor_after_first["offset"] > 0

    ids_after_first = {
        r["review_id"]
        for r in db.table("tripadvisor_reviews").select("review_id").eq("poi_id", test_poi).execute().data
    }

    second = scrape_poi_tripadvisor(test_poi, TREVI_BASE, pages_per_run=1, throttle_seconds=1)
    assert second["pages"] == 1
    ids_after_second = {
        r["review_id"]
        for r in db.table("tripadvisor_reviews").select("review_id").eq("poi_id", test_poi).execute().data
    }
    assert ids_after_second - ids_after_first, "second run should add new reviews"

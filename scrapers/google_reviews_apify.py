"""Google Maps reviews enrichment via Apify compass/Google-Maps-Reviews-Scraper.

Mirrors the vertical-slice shape of scrapers/google_reviews.py: parse, save,
fetch, orchestrate. Each slice is independently testable.
"""

from typing import Optional, TypedDict

from db.client import get_client

_SORT_MODE_MAP = {
    "mostRelevant": "most_relevant",
    "highestRanking": "highest",
    "lowestRanking": "lowest",
    "newest": "newest",
}

_MIN_TEXT_LEN = 75


class Review(TypedDict):
    review_id: str
    author: Optional[str]
    rating: Optional[int]
    text: str
    iso_date: Optional[str]
    likes: int
    is_local_guide: Optional[bool]
    reviewer_review_count: Optional[int]
    owner_response: Optional[str]
    review_url: Optional[str]
    original_language: Optional[str]
    sort_mode: str


def _coerce_rating(raw) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def parse_apify_items(
    items: list[dict],
    sort_mode: str,
    min_text_len: int = _MIN_TEXT_LEN,
) -> list[Review]:
    """Map raw Apify dataset items to our schema.

    Drops items with missing/empty review_id or text shorter than min_text_len.
    Normalizes sort_mode from Apify's camelCase to our snake_case convention.
    """
    normalized_sort = _SORT_MODE_MAP.get(sort_mode, sort_mode)
    seen: set[str] = set()
    out: list[Review] = []
    for item in items:
        review_id = item.get("reviewId")
        if not review_id or review_id in seen:
            continue
        seen.add(review_id)
        text = (item.get("text") or "").strip()
        if len(text) < min_text_len:
            continue
        out.append(Review(
            review_id=review_id,
            author=item.get("name"),
            rating=_coerce_rating(item.get("stars")),
            text=text,
            iso_date=item.get("publishedAtDate"),
            likes=item.get("likesCount") or 0,
            is_local_guide=item.get("isLocalGuide"),
            reviewer_review_count=item.get("reviewerNumberOfReviews"),
            owner_response=item.get("responseFromOwnerText"),
            review_url=item.get("reviewUrl"),
            original_language=item.get("originalLanguage"),
            sort_mode=normalized_sort,
        ))
    return out


def save_apify_reviews(poi_id: str, reviews: list[Review], sort_mode: str) -> int:
    """Upsert reviews into google_reviews, idempotent on review_id.

    Returns the number of rows written.
    """
    if not reviews:
        return 0
    db = get_client()
    rows = [{**r, "poi_id": poi_id} for r in reviews]
    result = db.table("google_reviews").upsert(rows, on_conflict="review_id").execute()
    return len(result.data or [])


def fetch_apify_reviews(
    place_id: str,
    sort_mode: str,
    max_reviews: int,
    *,
    apify_token: str,
    reviews_origin: str = "google",
    language: str = "en",
    max_total_charge_usd: Optional[float] = None,
) -> list[dict]:
    """Run compass/Google-Maps-Reviews-Scraper for one place+sort combo.

    Blocks until the actor run completes. Returns raw dataset items.
    """
    from datetime import timedelta
    from decimal import Decimal
    from apify_client import ApifyClient

    client = ApifyClient(apify_token)
    run_input = {
        "placeIds": [place_id],
        "maxReviews": max_reviews,
        "reviewsSort": sort_mode,
        "reviewsOrigin": reviews_origin,
        "language": language,
    }
    call_kwargs: dict = {
        "run_input": run_input,
        "run_timeout": timedelta(minutes=30),
    }
    if max_total_charge_usd is not None:
        call_kwargs["max_total_charge_usd"] = Decimal(str(max_total_charge_usd))

    run = client.actor("compass/Google-Maps-Reviews-Scraper").call(**call_kwargs)
    if run is None:
        raise RuntimeError("Apify actor call returned None")
    dataset_id = getattr(run, "default_dataset_id", None) or (run or {}).get("defaultDatasetId")
    if not dataset_id:
        status = getattr(run, "status", None) or (run or {}).get("status", "?")
        raise RuntimeError(f"Apify run has no defaultDatasetId (status={status})")
    return list(client.dataset(dataset_id).iterate_items())


def scrape_poi_google_apify(
    poi: dict,
    sort_configs: list[tuple[str, int]],
    *,
    apify_token: str,
    max_total_charge_usd: float,
    min_text_len: int = _MIN_TEXT_LEN,
) -> dict:
    """End-to-end Apify enrichment for one POI.

    sort_configs: list of (apify_sort_mode, max_reviews) pairs,
    e.g. [("mostRelevant", 2000), ("highestRanking", 1000), ("lowestRanking", 1000)].

    The budget cap is shared across all sort runs for this POI via Apify's
    max_total_charge_usd — caller is responsible for splitting it across POIs.

    Returns {saved, skipped, sort_results}.
    """
    poi_id = poi["id"]
    place_id = poi["place_id"]
    name = poi["name"]
    total_saved = 0
    total_skipped = 0
    sort_results = []

    for sort_mode, max_reviews in sort_configs:
        print(f"[apify-google] {name} | {sort_mode} | max={max_reviews}")
        raw_items = fetch_apify_reviews(
            place_id,
            sort_mode=sort_mode,
            max_reviews=max_reviews,
            apify_token=apify_token,
            max_total_charge_usd=max_total_charge_usd,
        )
        reviews = parse_apify_items(raw_items, sort_mode=sort_mode, min_text_len=min_text_len)
        skipped = len(raw_items) - len(reviews)
        saved = save_apify_reviews(poi_id, reviews, sort_mode=sort_mode)
        total_saved += saved
        total_skipped += skipped
        sort_results.append({"sort_mode": sort_mode, "fetched": len(raw_items), "saved": saved, "skipped": skipped})
        print(f"  fetched={len(raw_items)}, saved={saved}, skipped_short={skipped}")

    return {"saved": total_saved, "skipped": total_skipped, "sort_results": sort_results}

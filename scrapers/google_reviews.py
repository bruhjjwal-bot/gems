import os
import time
from typing import Optional, TypedDict

import requests

from db.client import get_client


class Review(TypedDict):
    review_id: str
    author: Optional[str]
    rating: Optional[int]
    text: Optional[str]
    relative_time: Optional[str]
    iso_date: Optional[str]
    likes: int


SORT_BY = {
    "most_relevant": "qualityScore",
    "newest": "newestFirst",
    "highest": "ratingHigh",
    "lowest": "ratingLow",
}

_SERPAPI_URL = "https://serpapi.com/search"

# Module-level throttle: any SerpApi call across the process honors the same min interval.
_LAST_REQUEST_AT: float = 0.0


def _throttle(min_interval: float) -> None:
    global _LAST_REQUEST_AT
    if min_interval > 0:
        delta = time.monotonic() - _LAST_REQUEST_AT
        if delta < min_interval:
            time.sleep(min_interval - delta)
    _LAST_REQUEST_AT = time.monotonic()


def build_maps_url(place_id: str) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def get_cursor(poi_id: str, sort_mode: str) -> dict:
    """Return {'token': Optional[str], 'exhausted': bool} for this (poi, sort_mode)."""
    rows = (
        get_client()
        .table("google_reviews_cursors")
        .select("next_page_token, exhausted")
        .eq("poi_id", poi_id)
        .eq("sort_mode", sort_mode)
        .execute()
        .data
    )
    if not rows:
        return {"token": None, "exhausted": False}
    row = rows[0]
    return {"token": row.get("next_page_token"), "exhausted": bool(row.get("exhausted"))}


def save_cursor(poi_id: str, sort_mode: str, *, token: Optional[str], exhausted: bool) -> None:
    get_client().table("google_reviews_cursors").upsert(
        {
            "poi_id": poi_id,
            "sort_mode": sort_mode,
            "next_page_token": token,
            "exhausted": exhausted,
        },
        on_conflict="poi_id,sort_mode",
    ).execute()


def dedupe_reviews(reviews: list[Review]) -> list[Review]:
    seen: set[str] = set()
    out: list[Review] = []
    for r in reviews:
        rid = r["review_id"]
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def _coerce_rating(raw) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def parse_reviews(serpapi_response: dict) -> list[Review]:
    raw_reviews = serpapi_response.get("reviews") or []
    out: list[Review] = []
    for r in raw_reviews:
        review_id = r.get("review_id")
        if not review_id:
            continue
        user = r.get("user") or {}
        out.append(
            Review(
                review_id=review_id,
                author=user.get("name"),
                rating=_coerce_rating(r.get("rating")),
                text=r.get("snippet") or (r.get("extracted_snippet") or {}).get("original"),
                relative_time=r.get("date"),
                iso_date=r.get("iso_date"),
                likes=r.get("likes") or 0,
            )
        )
    return out


def fetch_reviews(
    place_id: str,
    sort_mode: str = "most_relevant",
    *,
    start_token: Optional[str] = None,
    max_calls: int = 10,
    page_pause_seconds: float = 80.0,
) -> dict:
    """Paginate through SerpApi, capped at `max_calls` API requests this invocation.

    Returns:
        dict with 'reviews' (raw SerpApi review dicts), 'place_info' (first page),
        'last_token' (next_page_token after the last call, or None if exhausted),
        'exhausted' (True when SerpApi returned no further pages).

    If `start_token` is given, the first call uses it; otherwise starts from page 1.
    `page_pause_seconds` sleeps between API calls (default 80s to respect 50/hr cap).
    """
    if sort_mode not in SORT_BY:
        raise ValueError(f"unknown sort_mode {sort_mode!r}; expected one of {list(SORT_BY)}")
    if max_calls < 1:
        raise ValueError("max_calls must be >= 1")
    api_key = os.environ["SERPAPI_KEY"]

    aggregated: list[dict] = []
    place_info: dict = {}
    base_params: dict = {
        "engine": "google_maps_reviews",
        "place_id": place_id,
        "api_key": api_key,
        "hl": "en",
        "sort_by": SORT_BY[sort_mode],
    }
    next_token: Optional[str] = start_token
    exhausted = False
    calls_made = 0

    while calls_made < max_calls:
        _throttle(page_pause_seconds)
        params = dict(base_params)
        if next_token:
            params["next_page_token"] = next_token
            params["num"] = 20
        try:
            resp = requests.get(_SERPAPI_URL, params=params, timeout=180)
            resp.raise_for_status()
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            print(f"      transient error on call {calls_made + 1}: {type(e).__name__}; stopping this stream, cursor preserved")
            break
        calls_made += 1
        data = resp.json()
        if not place_info:
            place_info = data.get("place_info") or {}
        page_reviews = data.get("reviews") or []
        if not page_reviews:
            exhausted = True
            next_token = None
            break
        aggregated.extend(page_reviews)
        pagination = data.get("serpapi_pagination") or {}
        next_token = pagination.get("next_page_token")
        if not next_token:
            exhausted = True
            break

    return {
        "reviews": aggregated,
        "place_info": place_info,
        "last_token": next_token,
        "exhausted": exhausted,
        "calls_made": calls_made,
    }


def scrape_poi_google_reviews(
    poi_id: str,
    place_id: str,
    *,
    sort_modes: tuple[str, ...] = ("most_relevant",),
    calls_per_stream: int = 1,
    page_pause_seconds: float = 80.0,
) -> dict:
    """Resume-aware scrape: per sort_mode, pick up from saved cursor and do up to
    `calls_per_stream` SerpApi calls, then save the new cursor.

    Returns:
        dict with 'saved' (rows upserted), 'calls' (SerpApi calls made).
    """
    total_saved = 0
    total_calls = 0
    for sort_mode in sort_modes:
        cursor = get_cursor(poi_id, sort_mode)
        if cursor["exhausted"]:
            print(f"    sort={sort_mode}: exhausted, skipping")
            continue
        token_hint = "fresh" if not cursor["token"] else f"resume token …{cursor['token'][-12:]}"
        print(f"    sort={sort_mode}: {token_hint}, up to {calls_per_stream} calls")
        response = fetch_reviews(
            place_id,
            sort_mode=sort_mode,
            start_token=cursor["token"],
            max_calls=calls_per_stream,
            page_pause_seconds=page_pause_seconds,
        )
        total_calls += response["calls_made"]
        parsed = parse_reviews(response)
        if parsed:
            saved = save_reviews(poi_id, parsed, sort_mode=sort_mode)
            total_saved += saved
            print(f"    sort={sort_mode}: fetched {len(parsed)}, upserted {saved}")
        else:
            print(f"    sort={sort_mode}: no reviews returned")
        save_cursor(
            poi_id,
            sort_mode,
            token=response["last_token"],
            exhausted=response["exhausted"],
        )
        if response["exhausted"]:
            print(f"    sort={sort_mode}: SerpApi exhausted for this POI")
    return {"saved": total_saved, "calls": total_calls}


def save_reviews(poi_id: str, reviews: list[Review], sort_mode: str) -> int:
    if not reviews:
        return 0
    rows = [
        {
            "poi_id": poi_id,
            "review_id": r["review_id"],
            "author": r.get("author"),
            "rating": r.get("rating"),
            "text": r.get("text"),
            "relative_time": r.get("relative_time"),
            "iso_date": r.get("iso_date"),
            "likes": r.get("likes") or 0,
            "sort_mode": sort_mode,
        }
        for r in reviews
    ]
    result = (
        get_client()
        .table("google_reviews")
        .upsert(rows, on_conflict="review_id")
        .execute()
    )
    return len(result.data or [])

"""TripAdvisor reviews scraper via FireCrawl (stealth proxy).

Mirrors the vertical-slice shape of scrapers/google_reviews.py: URL builder,
parser, dedup, save, fetcher, orchestrator. Each slice is independently testable.
"""

import hashlib
import os
import re
import time
from typing import Optional, TypedDict

from db.client import get_client


class Review(TypedDict):
    review_id: str
    author: Optional[str]
    author_profile: Optional[str]
    rating: Optional[int]
    title: Optional[str]
    body: Optional[str]
    visit_date: Optional[str]
    trip_type: Optional[str]
    written_date: Optional[str]


_REVIEW_TITLE_RE = re.compile(
    r"(?:###\s+)?\[([^\]]+)\]\((https://www\.tripadvisor\.com/ShowUserReviews-[^)]*?-r(\d+)-[^)]*)\)"
)
_AUTHOR_RE = re.compile(
    r"\[([^\]]+)\]\(https://www\.tripadvisor\.com/Profile/([^)\s]+)\)"
)
_RATING_RE = re.compile(r"(\d(?:\.\d)?)\s+of\s+5\s+bubbles", re.IGNORECASE)
_VISIT_DATE_RE = re.compile(
    r"(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})"
    r"(?:\s+•\s+(?P<type>\w+))?"
)
_WRITTEN_DATE_RE = re.compile(r"Written\s+(\w+\s+\d{1,2},\s+\d{4})")
_HRULE_RE = re.compile(r"\n\s*\*\s*\*\s*\*\s*\n")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

# Module-level throttle: every FireCrawl call across the process honors the same
# minimum interval. Keeps us polite to TripAdvisor's edge defenses.
_LAST_REQUEST_AT: float = 0.0

# FireCrawl API-key management. We hold a primary and optional backup; when the
# primary's remaining credit balance drops below the threshold we transparently
# swap. Periodic credit checks every N calls to avoid hammering the metadata
# endpoint.
_ACTIVE_FC_KEY: Optional[str] = None
_CREDIT_CHECK_INTERVAL = 25
_CREDIT_LOW_THRESHOLD = 100
_CALLS_SINCE_CREDIT_CHECK = 0


def _throttle(min_interval: float) -> None:
    global _LAST_REQUEST_AT
    if min_interval > 0:
        delta = time.monotonic() - _LAST_REQUEST_AT
        if delta < min_interval:
            time.sleep(min_interval - delta)
    _LAST_REQUEST_AT = time.monotonic()


def _resolve_firecrawl_key() -> str:
    global _ACTIVE_FC_KEY
    if _ACTIVE_FC_KEY:
        return _ACTIVE_FC_KEY
    primary = os.environ.get("FIRECRAWL_API_KEY")
    if not primary:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    _ACTIVE_FC_KEY = primary
    print(f"      [creds] primary FireCrawl key loaded")
    return _ACTIVE_FC_KEY


def _maybe_swap_firecrawl_key() -> None:
    """Periodically check remaining credits; if primary is depleted and a backup
    key is configured via FIRECRAWL_API_KEY_BACKUP, switch to it. Credit
    remaining is logged unconditionally so progress can be tracked even when no
    backup is configured."""
    global _ACTIVE_FC_KEY, _CALLS_SINCE_CREDIT_CHECK
    _CALLS_SINCE_CREDIT_CHECK += 1
    if _CALLS_SINCE_CREDIT_CHECK < _CREDIT_CHECK_INTERVAL:
        return
    _CALLS_SINCE_CREDIT_CHECK = 0

    from firecrawl.v2 import FirecrawlClient

    try:
        client = FirecrawlClient(api_key=_ACTIVE_FC_KEY)
        usage = client.get_credit_usage()
        remaining = getattr(usage, "remaining_credits", None)
        if remaining is None:
            return
        print(f"      [creds] {remaining} credits remaining on active key")
        backup = os.environ.get("FIRECRAWL_API_KEY_BACKUP")
        if backup and remaining < _CREDIT_LOW_THRESHOLD and _ACTIVE_FC_KEY != backup:
            print(f"      [creds] below threshold ({_CREDIT_LOW_THRESHOLD}); swapping to backup key")
            _ACTIVE_FC_KEY = backup
    except Exception as e:
        print(f"      [creds] credit check skipped: {type(e).__name__}: {e}")


def build_tripadvisor_url(base_url: str, offset: int = 0) -> str:
    """Splice -or{offset}- into the canonical TripAdvisor reviews URL.

    offset=0 returns the base URL unchanged (TripAdvisor's default first-page URL).
    Offsets advance in steps of 10 (one review-list page per step).
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if offset == 0:
        return base_url
    if "-Reviews-" not in base_url:
        raise ValueError(f"base_url missing '-Reviews-' segment: {base_url}")
    return base_url.replace("-Reviews-", f"-Reviews-or{offset}-", 1)


def _coerce_rating(raw: str) -> Optional[int]:
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def _synthetic_review_id(
    author: Optional[str],
    body: Optional[str],
    visit_date: Optional[str],
    written_date: Optional[str],
) -> str:
    """Deterministic synthetic review_id for review blocks lacking a permalink.

    Used on TripAdvisor's deep-pagination "variant-3" pages, where the body /
    rating / dates render fine but the ShowUserReviews permalink is stripped.
    The `syn_` prefix lets a future backfill job find these and replace them
    with real IDs once a recovery path exists.
    """
    def norm(s: Optional[str]) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).lower()

    payload = "|".join([
        norm(author),
        norm(body)[:200],
        norm(visit_date),
        norm(written_date),
    ])
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"syn_{h}"


def _extract_variant3_title(after_rating: str) -> Optional[str]:
    """The first non-empty line after the rating block that is not itself a
    visit-date pattern. Variant-3 review blocks render the title as plain text
    rather than a markdown link."""
    for raw_line in after_rating.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _VISIT_DATE_RE.match(line):
            return None
        return line
    return None


def parse_reviews(firecrawl_response: dict) -> list[Review]:
    """Extract reviews from a FireCrawl markdown payload.

    Splits the page by horizontal rules, looks for review header lines of the
    form `### [Title](ShowUserReviews-...-r{id}-...)`, then pulls author,
    rating, visit_date, trip_type, body, and written_date from the surrounding
    segment. Unparseable fields become None.
    """
    md = firecrawl_response.get("markdown") or ""
    out: list[Review] = []
    seen_ids: set[str] = set()

    for seg in _HRULE_RE.split(md):
        # Every review block has both a star-rating and a "Written {date}" line.
        rating_m = _RATING_RE.search(seg)
        written_m = _WRITTEN_DATE_RE.search(seg)
        if not (rating_m and written_m):
            continue

        # Try variant 1/2 first: explicit ShowUserReviews permalink.
        title_m = _REVIEW_TITLE_RE.search(seg)
        if title_m:
            review_id: Optional[str] = "r" + title_m.group(3)
            title = title_m.group(1).strip()
            before = seg[: title_m.start()]
            after = seg[title_m.end():]
        else:
            # Variant 3: no permalink in markdown OR html. Title is plain text.
            title = _extract_variant3_title(seg[rating_m.end():])
            if not title:
                continue
            review_id = None  # synthesized below once we have author/body/dates
            title_pos = seg.find(title, rating_m.end())
            before = seg[:rating_m.end()]
            after = seg[title_pos + len(title):] if title_pos >= 0 else seg[rating_m.end():]

        # Author: the last `[Name](Profile/slug)` link before the title (the
        # avatar line's bracket pair contains an image and won't match this regex).
        author = None
        author_profile = None
        author_matches = list(_AUTHOR_RE.finditer(before))
        if author_matches:
            am = author_matches[-1]
            author = am.group(1).strip()
            author_profile = am.group(2).strip()

        rating = _coerce_rating(rating_m.group(1))

        visit_m = _VISIT_DATE_RE.search(after)
        visit_date = visit_m.group("date") if visit_m else None
        trip_type = visit_m.group("type") if visit_m else None

        body: Optional[str] = None
        if visit_m:
            body_start = visit_m.end()
            stop_idx: Optional[int] = None
            for marker in ("\nRead more\n", "\nWritten "):
                idx = after.find(marker, body_start)
                if idx >= 0 and (stop_idx is None or idx < stop_idx):
                    stop_idx = idx
            if stop_idx is None:
                stop_idx = len(after)
            body_raw = after[body_start:stop_idx]
            body_clean = _IMG_RE.sub("", body_raw).strip()
            body = body_clean or None

        written_date = written_m.group(1)

        if review_id is None:
            review_id = _synthetic_review_id(author, body, visit_date, written_date)

        if review_id in seen_ids:
            continue
        seen_ids.add(review_id)

        out.append(
            Review(
                review_id=review_id,
                author=author,
                author_profile=author_profile,
                rating=rating,
                title=title,
                body=body,
                visit_date=visit_date,
                trip_type=trip_type,
                written_date=written_date,
            )
        )

    return out


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


def save_reviews(poi_id: str, reviews: list[Review], sort_mode: str) -> int:
    if not reviews:
        return 0
    rows = [
        {
            "poi_id": poi_id,
            "review_id": r["review_id"],
            "author": r.get("author"),
            "author_profile": r.get("author_profile"),
            "rating": r.get("rating"),
            "title": r.get("title"),
            "body": r.get("body"),
            "visit_date": r.get("visit_date"),
            "trip_type": r.get("trip_type"),
            "written_date": r.get("written_date"),
            "sort_mode": sort_mode,
        }
        for r in reviews
    ]
    result = (
        get_client()
        .table("tripadvisor_reviews")
        .upsert(rows, on_conflict="review_id")
        .execute()
    )
    return len(result.data or [])


def get_cursor(poi_id: str, sort_mode: str) -> dict:
    """Return {'offset': int, 'exhausted': bool} for this (poi, sort_mode)."""
    rows = (
        get_client()
        .table("tripadvisor_review_cursors")
        .select("next_offset, exhausted")
        .eq("poi_id", poi_id)
        .eq("sort_mode", sort_mode)
        .execute()
        .data
    )
    if not rows:
        return {"offset": 0, "exhausted": False}
    row = rows[0]
    return {"offset": row.get("next_offset") or 0, "exhausted": bool(row.get("exhausted"))}


def save_cursor(poi_id: str, sort_mode: str, *, offset: int, exhausted: bool) -> None:
    get_client().table("tripadvisor_review_cursors").upsert(
        {
            "poi_id": poi_id,
            "sort_mode": sort_mode,
            "next_offset": offset,
            "exhausted": exhausted,
        },
        on_conflict="poi_id,sort_mode",
    ).execute()


def fetch_reviews_page(base_url: str, offset: int, *, throttle_seconds: float = 5.0) -> dict:
    """One FireCrawl call with stealth proxy. Returns the raw payload dict.

    Throws on transport errors; the orchestrator catches and decides whether to
    persist progress before re-raising.
    """
    from firecrawl.v2 import FirecrawlClient

    _throttle(throttle_seconds)
    _maybe_swap_firecrawl_key()
    api_key = _resolve_firecrawl_key()
    client = FirecrawlClient(api_key=api_key)
    url = build_tripadvisor_url(base_url, offset)
    doc = client.scrape(
        url=url,
        formats=["markdown", "html"],
        proxy="stealth",
        wait_for=3000,
        only_main_content=False,
    )
    return doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)


def scrape_poi_tripadvisor(
    poi_id: str,
    base_url: str,
    *,
    pages_per_run: int = 1,
    throttle_seconds: float = 5.0,
    sort_mode: str = "most_recent",
) -> dict:
    """Resume-aware: load cursor, fetch up to N pages, save reviews, advance cursor.

    Returns dict with 'saved', 'pages', 'exhausted'.
    """
    cursor = get_cursor(poi_id, sort_mode)
    if cursor["exhausted"]:
        print(f"    sort={sort_mode}: exhausted, skipping")
        return {"saved": 0, "pages": 0, "exhausted": True}

    offset = cursor["offset"]
    saved_total = 0
    pages_fetched = 0
    exhausted = False

    print(f"    sort={sort_mode}: resume offset={offset}, up to {pages_per_run} pages")
    for i in range(pages_per_run):
        try:
            payload = fetch_reviews_page(base_url, offset, throttle_seconds=throttle_seconds)
        except Exception as e:
            # Transport-level error (FireCrawl proxy / network). Cursor is NOT
            # advanced — next round retries the same offset.
            print(f"      fetch error at offset={offset}: {type(e).__name__}: {e}; preserving cursor")
            break
        parsed = parse_reviews(payload)
        if not parsed:
            # We received a valid response but extracted no reviews. Causes
            # observed: TripAdvisor renders the page without any review section
            # at certain offsets (Eiffel offset=240 has 0 review blocks despite
            # being a valid 94KB page), or deep-pagination variants we don't
            # yet parse. Advance the cursor anyway — leaving it stuck means
            # every future round burns another fetch on the same dead offset.
            # Combined with the orchestrator's error_streak the POI walks past
            # walls 10 offsets at a time and gets skipped for the rest of the
            # run after 3 consecutive misses.
            print(f"      no reviews parsed at offset={offset}; advancing past wall (offset += 10) and counting as error")
            offset += 10
            break
        # Only count fully-successful pages toward `pages`; this lets the
        # caller distinguish "fetch + parse worked" from "fetched empty".
        pages_fetched += 1
        deduped = dedupe_reviews(parsed)
        saved = save_reviews(poi_id, deduped, sort_mode=sort_mode)
        saved_total += saved
        print(f"      offset={offset}: parsed {len(parsed)}, upserted {saved}")
        offset += 10

    save_cursor(poi_id, sort_mode, offset=offset, exhausted=exhausted)
    return {"saved": saved_total, "pages": pages_fetched, "exhausted": exhausted}

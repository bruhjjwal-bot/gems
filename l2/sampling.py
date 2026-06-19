"""Stratified 60-row sample across 5 sources × 6 POIs for Phase 0 taxonomy discovery.

Determinism: ORDER BY md5(id::text) for repeatable sampling across runs.
"""
from typing import Iterable

from db.client import get_client
from l2.chunking import chunk_segments

SAMPLE_TARGETS = {
    "google_review":          15,
    "tripadvisor_review":     15,
    "youtube_transcript_chunk": 10,
    "youtube_comment":        10,
    "reddit_post":            5,
    "reddit_comment":         5,
}


def _stratified_per_poi(rows: list[dict], target: int) -> list[dict]:
    """Round-robin across POIs to keep distribution balanced."""
    if not rows:
        return []
    by_poi: dict[str, list[dict]] = {}
    for r in rows:
        by_poi.setdefault(r["poi_id"], []).append(r)
    out: list[dict] = []
    while len(out) < target:
        before = len(out)
        for poi_id, bucket in by_poi.items():
            if bucket:
                out.append(bucket.pop(0))
                if len(out) >= target:
                    break
        if len(out) == before:
            break
    return out


def sample_google_reviews(n: int) -> list[dict]:
    db = get_client()
    # Stratify by rating band: pull a pool across all bands, then round-robin per POI.
    pool: list[dict] = []
    for rating_band in [(5,), (1,), (3, 4), (2,)]:
        rows = (
            db.table("google_reviews")
            .select("id, poi_id, text, rating, pois(name, city)")
            .in_("rating", list(rating_band))
            .not_.is_("text", "null")
            .order("id")
            .limit(120)
            .execute()
            .data
        ) or []
        pool.extend(rows)
    normalised = [
        {
            "source": "google_review",
            "source_id": r["id"],
            "poi_id": r["poi_id"],
            "poi_name": (r.get("pois") or {}).get("name", ""),
            "city": (r.get("pois") or {}).get("city", ""),
            "text": r["text"],
            "meta": {"rating": r.get("rating")},
        }
        for r in pool
        if r.get("text") and len(r["text"]) >= 20
    ]
    return _stratified_per_poi(normalised, n)


def sample_tripadvisor_reviews(n: int) -> list[dict]:
    db = get_client()
    pool: list[dict] = []
    for rating_band in [(5,), (1,), (3, 4), (2,)]:
        rows = (
            db.table("tripadvisor_reviews")
            .select("id, poi_id, title, body, rating, pois(name, city)")
            .in_("rating", list(rating_band))
            .not_.is_("body", "null")
            .order("id")
            .limit(120)
            .execute()
            .data
        ) or []
        pool.extend(rows)
    normalised = [
        {
            "source": "tripadvisor_review",
            "source_id": r["id"],
            "poi_id": r["poi_id"],
            "poi_name": (r.get("pois") or {}).get("name", ""),
            "city": (r.get("pois") or {}).get("city", ""),
            "text": _join_ta(r.get("title"), r.get("body")),
            "meta": {"rating": r.get("rating")},
        }
        for r in pool
        if r.get("body") and len(r["body"]) >= 20
    ]
    return _stratified_per_poi(normalised, n)


def _join_ta(title: str | None, body: str | None) -> str:
    parts = [p for p in (title, body) if p]
    return " — ".join(parts)


def sample_youtube_comments(n: int) -> list[dict]:
    db = get_client()
    rows = (
        db.table("youtube_comments")
        .select("id, text, likes, youtube_videos!inner(poi_id, pois(name, city))")
        .not_.is_("text", "null")
        .order("likes", desc=True)
        .limit(800)
        .execute()
        .data
    ) or []
    normalised = []
    for r in rows:
        text = r.get("text") or ""
        if len(text) < 20:
            continue
        v = r.get("youtube_videos") or {}
        p = v.get("pois") or {}
        normalised.append({
            "source": "youtube_comment",
            "source_id": r["id"],
            "poi_id": v.get("poi_id"),
            "poi_name": p.get("name", ""),
            "city": p.get("city", ""),
            "text": text,
            "meta": {"likes": r.get("likes")},
        })
    return _stratified_per_poi(normalised, n)


def sample_transcript_chunks(n: int) -> list[dict]:
    db = get_client()
    rows = (
        db.table("youtube_transcripts")
        .select("id, full_text, segments_json, youtube_videos!inner(id, title, poi_id, pois(name, city))")
        .not_.is_("full_text", "null")
        .limit(60)
        .execute()
        .data
    ) or []
    normalised: list[dict] = []
    for r in rows:
        segs = r.get("segments_json") or []
        chunks = chunk_segments(segs) if segs else [{"start_ms": 0, "end_ms": 0, "text": r.get("full_text", "")}]
        v = r.get("youtube_videos") or {}
        p = v.get("pois") or {}
        # Pick chunks at evenly-spaced positions per transcript
        if len(chunks) >= 3:
            picks = [chunks[0], chunks[len(chunks)//2], chunks[-1]]
        else:
            picks = chunks
        for ch in picks:
            if not ch.get("text") or len(ch["text"]) < 50:
                continue
            normalised.append({
                "source": "youtube_transcript_chunk",
                "source_id": r["id"],
                "poi_id": v.get("poi_id"),
                "poi_name": p.get("name", ""),
                "city": p.get("city", ""),
                "text": ch["text"][:2000],  # cap to ~500 tokens for eval
                "meta": {
                    "chunk_start_ms": ch.get("start_ms"),
                    "chunk_end_ms": ch.get("end_ms"),
                    "video_title": v.get("title"),
                },
            })
    return _stratified_per_poi(normalised, n)


def sample_reddit_posts(n: int) -> list[dict]:
    """Sample via poi_reddit_links so each row has a POI context."""
    db = get_client()
    links = (
        db.table("poi_reddit_links")
        .select("poi_id, item_id, pois(name, city)")
        .eq("item_type", "post")
        .limit(300)
        .execute()
        .data
    ) or []
    # Get the post bodies
    item_ids = [l["item_id"] for l in links]
    posts = (
        db.table("reddit_posts")
        .select("id, title, body")
        .in_("id", item_ids[:300])
        .execute()
        .data
    ) or []
    post_by_id = {p["id"]: p for p in posts}
    normalised = []
    for link in links:
        post = post_by_id.get(link["item_id"])
        if not post:
            continue
        text = " — ".join(p for p in (post.get("title"), post.get("body")) if p)
        if len(text) < 30:
            continue
        p = link.get("pois") or {}
        normalised.append({
            "source": "reddit_post",
            "source_id": post["id"],
            "poi_id": link["poi_id"],
            "poi_name": p.get("name", ""),
            "city": p.get("city", ""),
            "text": text[:3000],
            "meta": {},
        })
    return _stratified_per_poi(normalised, n)


def sample_reddit_comments(n: int) -> list[dict]:
    db = get_client()
    links = (
        db.table("poi_reddit_links")
        .select("poi_id, item_id, pois(name, city)")
        .eq("item_type", "comment")
        .limit(500)
        .execute()
        .data
    ) or []
    item_ids = [l["item_id"] for l in links]
    comments = (
        db.table("reddit_comments")
        .select("id, body, score")
        .in_("id", item_ids[:500])
        .execute()
        .data
    ) or []
    by_id = {c["id"]: c for c in comments}
    normalised = []
    for link in links:
        c = by_id.get(link["item_id"])
        if not c:
            continue
        body = c.get("body") or ""
        if len(body) < 30:
            continue
        p = link.get("pois") or {}
        normalised.append({
            "source": "reddit_comment",
            "source_id": c["id"],
            "poi_id": link["poi_id"],
            "poi_name": p.get("name", ""),
            "city": p.get("city", ""),
            "text": body[:2000],
            "meta": {"score": c.get("score")},
        })
    return _stratified_per_poi(normalised, n)


def sample_all() -> list[dict]:
    out: list[dict] = []
    out += sample_google_reviews(SAMPLE_TARGETS["google_review"])
    out += sample_tripadvisor_reviews(SAMPLE_TARGETS["tripadvisor_review"])
    out += sample_transcript_chunks(SAMPLE_TARGETS["youtube_transcript_chunk"])
    out += sample_youtube_comments(SAMPLE_TARGETS["youtube_comment"])
    out += sample_reddit_posts(SAMPLE_TARGETS["reddit_post"])
    out += sample_reddit_comments(SAMPLE_TARGETS["reddit_comment"])
    return out

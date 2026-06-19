"""Source-specific fetchers for the multi-source insight pipeline.

All fetchers return rows with this common shape:
  {
    "source": "<source_name>",      # tag for downstream
    "source_id": str,                # unique stable id (review_id, comment_id, etc.)
    "source_uuid": str,              # FK uuid in the source table
    "poi_id": str, "poi_name": str, "city": str,
    "rating": int | None,            # if applicable
    "text": str,                     # the body to extract from
    "meta": {...},                   # source-specific metadata
  }
"""
from db.client import get_client
from l2.chunking import chunk_segments


def fetch_google_reviews(poi_id: str, poi_name: str, city: str, target: int) -> list[dict]:
    db = get_client()
    rows = (
        db.table("google_reviews")
        .select("id, review_id, rating, text, likes, original_language")
        .eq("poi_id", poi_id)
        .not_.is_("text", "null")
        .order("likes", desc=True)
        .limit(target * 2)
        .execute()
        .data
    ) or []
    out: list[dict] = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if len(text) < 40:
            continue
        out.append({
            "source": "google_review",
            "source_id": r["review_id"],
            "source_uuid": r["id"],
            "poi_id": poi_id,
            "poi_name": poi_name,
            "city": city,
            "rating": r.get("rating"),
            "text": text[:3000],
            "meta": {"likes": r.get("likes"), "lang": r.get("original_language")},
        })
        if len(out) >= target:
            break
    return out


def fetch_youtube_comments(poi_id: str, poi_name: str, city: str, target: int) -> list[dict]:
    db = get_client()
    rows = (
        db.table("youtube_comments")
        .select("id, comment_id, text, likes, youtube_videos!inner(poi_id)")
        .eq("youtube_videos.poi_id", poi_id)
        .not_.is_("text", "null")
        .order("likes", desc=True)
        .limit(target * 3)
        .execute()
        .data
    ) or []
    out: list[dict] = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if len(text) < 40:
            continue
        out.append({
            "source": "youtube_comment",
            "source_id": r["comment_id"],
            "source_uuid": r["id"],
            "poi_id": poi_id,
            "poi_name": poi_name,
            "city": city,
            "rating": None,
            "text": text[:1500],
            "meta": {"likes": r.get("likes")},
        })
        if len(out) >= target:
            break
    return out


def fetch_youtube_transcript_chunks(poi_id: str, poi_name: str, city: str, target: int) -> list[dict]:
    db = get_client()
    rows = (
        db.table("youtube_transcripts")
        .select("id, full_text, segments_json, youtube_videos!inner(id, title, poi_id)")
        .eq("youtube_videos.poi_id", poi_id)
        .not_.is_("full_text", "null")
        .execute()
        .data
    ) or []
    out: list[dict] = []
    for r in rows:
        segs = r.get("segments_json") or []
        chunks = chunk_segments(segs) if segs else []
        v = r.get("youtube_videos") or {}
        for ch in chunks:
            txt = (ch.get("text") or "").strip()
            if len(txt) < 80:
                continue
            out.append({
                "source": "youtube_transcript_chunk",
                "source_id": f"{r['id']}:{ch.get('start_ms', 0)}",
                "source_uuid": r["id"],
                "poi_id": poi_id,
                "poi_name": poi_name,
                "city": city,
                "rating": None,
                "text": txt[:2500],
                "meta": {
                    "video_title": v.get("title"),
                    "chunk_start_ms": ch.get("start_ms"),
                    "chunk_end_ms": ch.get("end_ms"),
                },
            })
            if len(out) >= target:
                return out
    return out


def _batch_in(db, table: str, select: str, column: str, ids: list[str], chunk: int = 100) -> list[dict]:
    """Fetch rows where column IN ids, chunking to avoid URL-length limits."""
    out = []
    for i in range(0, len(ids), chunk):
        rows = (
            db.table(table)
            .select(select)
            .in_(column, ids[i:i + chunk])
            .execute()
            .data
        ) or []
        out.extend(rows)
    return out


def fetch_reddit_posts(poi_id: str, poi_name: str, city: str, target: int) -> list[dict]:
    db = get_client()
    links = (
        db.table("poi_reddit_links")
        .select("item_id")
        .eq("poi_id", poi_id)
        .eq("item_type", "post")
        .limit(target * 3)
        .execute()
        .data
    ) or []
    item_ids = list({l["item_id"] for l in links})
    if not item_ids:
        return []
    posts = _batch_in(db, "reddit_posts", "id, reddit_id, title, body, score", "id", item_ids)
    posts.sort(key=lambda p: -(p.get("score") or 0))
    out: list[dict] = []
    for p in posts:
        body = (p.get("body") or "").strip()
        title = (p.get("title") or "").strip()
        text = f"{title}\n\n{body}" if body else title
        if len(text) < 40:
            continue
        out.append({
            "source": "reddit_post",
            "source_id": p["reddit_id"],
            "source_uuid": p["id"],
            "poi_id": poi_id,
            "poi_name": poi_name,
            "city": city,
            "rating": None,
            "text": text[:3500],
            "meta": {"score": p.get("score")},
        })
        if len(out) >= target:
            break
    return out


def fetch_reddit_comments(poi_id: str, poi_name: str, city: str, target: int) -> list[dict]:
    db = get_client()
    links = (
        db.table("poi_reddit_links")
        .select("item_id")
        .eq("poi_id", poi_id)
        .eq("item_type", "comment")
        .limit(target * 5)
        .execute()
        .data
    ) or []
    item_ids = list({l["item_id"] for l in links})
    if not item_ids:
        return []
    comments = _batch_in(db, "reddit_comments", "id, reddit_id, body, score, post_score", "id", item_ids)
    comments.sort(key=lambda c: -(c.get("score") or 0))
    out: list[dict] = []
    for c in comments:
        body = (c.get("body") or "").strip()
        if len(body) < 40:
            continue
        out.append({
            "source": "reddit_comment",
            "source_id": c["reddit_id"],
            "source_uuid": c["id"],
            "poi_id": poi_id,
            "poi_name": poi_name,
            "city": city,
            "rating": None,
            "text": body[:2500],
            "meta": {"score": c.get("score"), "post_score": c.get("post_score")},
        })
        if len(out) >= target:
            break
    return out

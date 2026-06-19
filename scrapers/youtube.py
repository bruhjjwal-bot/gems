from typing import Optional
from datetime import datetime, timezone
import time
import requests
import yt_dlp
from dotenv import load_dotenv
from db.client import get_client

CAPTION_THROTTLE_SECONDS = 4.0
CAPTION_MAX_RETRIES = 3
_LAST_CAPTION_FETCH_AT = 0.0

load_dotenv()

QUERY_TEMPLATES = ["{} tips", "{} visit guide", "{} review", "{} itinerary"]
EXTRA_QUERY_TEMPLATES = [
    "{} walkthrough",
    "{} hidden gems",
    "{} things to know",
    "best of {}",
    "things to do at {}",
    "{} guided tour",
    "{} history",
    "{} vlog",
    "{} secrets",
    "first time visiting {}",
]
MAX_RESULTS_PER_QUERY = 10
MAX_VIDEOS_PER_POI = 40
MIN_VIEWS_LONG = 10_000
MIN_VIEWS_SHORT = 5_000
MIN_DURATION_SECONDS = 30

BASE_OPTS = {"quiet": True, "skip_download": True, "no_warnings": True, "ignoreerrors": True}


def _search_videos(query: str) -> list[dict]:
    print(f"  Searching: {query!r}")
    opts = {**BASE_OPTS, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{MAX_RESULTS_PER_QUERY}:{query}", download=False)
        return info.get("entries") or []


def _parse_flat_entry(entry: dict) -> dict:
    duration = entry.get("duration") or 0
    views = entry.get("view_count") or 0
    return {
        "video_id": entry["id"],
        "title": entry.get("title", ""),
        "url": f"https://www.youtube.com/watch?v={entry['id']}",
        "format": "short" if duration <= 60 else "long",
        "views": views,
        "likes": entry.get("like_count") or 0,
        "comment_count": entry.get("comment_count") or 0,
        "duration_seconds": int(duration),
        "published_at": entry.get("upload_date"),
    }


def _score(video: dict) -> float:
    view_score = min(video["views"] / 500_000, 1.0)
    engagement = video["likes"] / max(video["views"], 1)
    engagement_score = min(engagement / 0.05, 1.0)
    comment_density = video["comment_count"] / max(video["views"], 1)
    comment_score = min(comment_density / 0.01, 1.0)
    return round(0.5 * view_score + 0.3 * engagement_score + 0.2 * comment_score, 4)


def _filter_and_score(videos: list[dict]) -> list[dict]:
    survivors = []
    for v in videos:
        min_views = MIN_VIEWS_SHORT if v["format"] == "short" else MIN_VIEWS_LONG
        if v["views"] >= min_views and v["duration_seconds"] >= MIN_DURATION_SECONDS:
            v["score"] = _score(v)
            survivors.append(v)
    survivors.sort(key=lambda v: v["score"], reverse=True)
    return survivors[:MAX_VIDEOS_PER_POI]


def _fetch_full_info(video_id: str) -> Optional[dict]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        **BASE_OPTS,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "getcomments": True,
        "extractor_args": {"youtube": {"comment_sort": ["top"], "max_comments": ["100"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"    Full info error: {e}")
        return None


def _fetch_caption_json(url: str) -> Optional[dict]:
    """Throttled fetch with exponential backoff on 429."""
    global _LAST_CAPTION_FETCH_AT
    backoff = CAPTION_THROTTLE_SECONDS
    for attempt in range(CAPTION_MAX_RETRIES):
        elapsed = time.time() - _LAST_CAPTION_FETCH_AT
        if elapsed < CAPTION_THROTTLE_SECONDS:
            time.sleep(CAPTION_THROTTLE_SECONDS - elapsed)
        try:
            resp = requests.get(url, timeout=30)
            _LAST_CAPTION_FETCH_AT = time.time()
            if resp.status_code == 429:
                print(f"    429 rate limited; backing off {backoff:.0f}s (attempt {attempt + 1}/{CAPTION_MAX_RETRIES})")
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"    Caption fetch failed: {e}")
            return None
    return None


def _extract_transcript(info: dict) -> Optional[dict]:
    subs = info.get("subtitles", {}).get("en") or info.get("automatic_captions", {}).get("en")
    if not subs:
        return None
    json3 = next((s for s in subs if s.get("ext") == "json3"), None)
    if not json3 or not json3.get("url"):
        return None
    data = _fetch_caption_json(json3["url"])
    if not data:
        return None
    segments = []
    text_parts = []
    for event in data.get("events", []) or []:
        segs = event.get("segs")
        if not segs:
            continue
        line = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not line:
            continue
        segments.append({
            "start_ms": event.get("tStartMs", 0),
            "duration_ms": event.get("dDurationMs", 0),
            "text": line,
        })
        text_parts.append(line)
    if not text_parts:
        return None
    return {
        "language": "en",
        "segments_json": segments,
        "full_text": " ".join(text_parts),
    }


def scrape_additional_videos_for_poi(
    poi_id: str,
    poi_name: str,
    target_new: int = 20,
    templates: Optional[list[str]] = None,
):
    """Find target_new MORE videos for an existing POI using broader search templates.
    Dedupes against videos already in the DB, applies the same engagement filter
    (views >= MIN_VIEWS_*, duration >= MIN_DURATION_SECONDS), then takes the top N
    new ones by score. Saves video metadata, transcripts, and comments."""
    db = get_client()
    existing = (
        db.table("youtube_videos")
        .select("video_id")
        .eq("poi_id", poi_id)
        .execute()
        .data
        or []
    )
    existing_ids = {r["video_id"] for r in existing}
    print(f"\nExpanding YouTube videos for {poi_name} (have {len(existing_ids)}, want +{target_new})")

    used_templates = templates or (QUERY_TEMPLATES + EXTRA_QUERY_TEMPLATES)
    seen: set[str] = set(existing_ids)
    candidates: list[dict] = []
    for template in used_templates:
        for entry in _search_videos(template.format(poi_name)):
            vid_id = entry.get("id")
            if vid_id and vid_id not in seen:
                seen.add(vid_id)
                candidates.append(_parse_flat_entry(entry))
    print(f"  Found {len(candidates)} new candidate videos after dedup")

    survivors = []
    for v in candidates:
        min_views = MIN_VIEWS_SHORT if v["format"] == "short" else MIN_VIEWS_LONG
        if v["views"] >= min_views and v["duration_seconds"] >= MIN_DURATION_SECONDS:
            v["score"] = _score(v)
            survivors.append(v)
    survivors.sort(key=lambda v: v["score"], reverse=True)
    shortlisted = survivors[:target_new]
    print(f"  Shortlisted {len(shortlisted)} new videos (of {len(survivors)} passing engagement)")

    saved_videos = 0
    saved_transcripts = 0
    saved_comments_total = 0
    for video in shortlisted:
        print(f"  Processing: {video['title'][:70]}")
        info = _fetch_full_info(video["video_id"])
        video_uuid = _save_video(poi_id, video)
        if not video_uuid:
            print("    save video failed; skipping")
            continue
        saved_videos += 1
        if info:
            transcript = _extract_transcript(info)
            if transcript:
                _save_transcript(video_uuid, transcript)
                saved_transcripts += 1
                print(f"    transcript: {len(transcript['full_text'])} chars")
            else:
                print("    transcript: none")
            comments = info.get("comments") or []
            _save_comments(video_uuid, comments)
            saved_comments_total += len(comments)
            print(f"    comments: {len(comments)}")
        else:
            print("    full info fetch failed")

    print(
        f"\nDone. {poi_name}: +{saved_videos} videos, "
        f"+{saved_transcripts} transcripts, +{saved_comments_total} comments."
    )


def refresh_transcripts_for_poi(poi_id: str, poi_name: str):
    """Re-run transcript extraction for every existing video tied to this POI.
    Videos and comments are left untouched; only the transcripts row is rewritten."""
    db = get_client()
    videos = (
        db.table("youtube_videos")
        .select("id, video_id, title")
        .eq("poi_id", poi_id)
        .execute()
        .data
        or []
    )
    print(f"\nRefreshing transcripts for {poi_name}: {len(videos)} videos")
    saved = 0
    for v in videos:
        print(f"  {v['title'][:70]}")
        info = _fetch_full_info(v["video_id"])
        if not info:
            print("    fetch failed")
            continue
        transcript = _extract_transcript(info)
        if not transcript:
            print("    no transcript available")
            continue
        _save_transcript(v["id"], transcript)
        chars = len(transcript["full_text"])
        print(f"    saved ({chars} chars, {len(transcript['segments_json'])} segments)")
        saved += 1
    print(f"\nDone. Refreshed {saved}/{len(videos)} transcripts for {poi_name}.")


def _save_video(poi_id: str, video: dict) -> Optional[str]:
    db = get_client()
    result = db.table("youtube_videos").upsert(
        {**video, "poi_id": poi_id},
        on_conflict="video_id",
    ).execute()
    rows = result.data
    return rows[0]["id"] if rows else None


def _save_transcript(video_uuid: str, transcript: dict):
    get_client().table("youtube_transcripts").upsert(
        {
            "video_id": video_uuid,
            "language": transcript["language"],
            "full_text": transcript.get("full_text"),
            "segments_json": transcript.get("segments_json"),
        },
        on_conflict="video_id",
    ).execute()


def _save_comments(video_uuid: str, comments: list[dict]):
    if not comments:
        return
    rows = [
        {
            "video_id": video_uuid,
            "comment_id": c["id"],
            "author": c.get("author"),
            "text": c.get("text"),
            "likes": c.get("like_count", 0),
            "published_at": datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).isoformat() if c.get("timestamp") else None,
        }
        for c in comments
        if c.get("id") and c.get("text")
    ]
    if rows:
        get_client().table("youtube_comments").upsert(rows, on_conflict="comment_id").execute()


def scrape_poi_youtube(poi_id: str, poi_name: str):
    print(f"\nScraping YouTube for: {poi_name}")

    # 1. Search — use flat extraction (no format resolution, just metadata)
    all_terms = [poi_name]
    seen_ids: set[str] = set()
    candidates: list[dict] = []

    for term in all_terms:
        for template in QUERY_TEMPLATES:
            for entry in _search_videos(template.format(term)):
                vid_id = entry.get("id")
                if vid_id and vid_id not in seen_ids:
                    seen_ids.add(vid_id)
                    candidates.append(_parse_flat_entry(entry))

    print(f"  Found {len(candidates)} unique candidate videos")

    # 2. Filter and score using flat metadata (views + duration available from search)
    shortlisted = _filter_and_score(candidates)
    print(f"  Shortlisted {len(shortlisted)} videos after filter/score")

    # 3. For shortlisted videos only — fetch full info (transcripts + comments)
    for video in shortlisted:
        print(f"  Processing: {video['title'][:60]}")

        info = _fetch_full_info(video["video_id"])

        video_uuid = _save_video(poi_id, video)
        if not video_uuid:
            print(f"    Could not save video, skipping")
            continue

        if info:
            transcript = _extract_transcript(info)
            if transcript:
                _save_transcript(video_uuid, transcript)
                print(f"    Transcript saved")
            else:
                print(f"    No transcript available")

            comments = info.get("comments") or []
            _save_comments(video_uuid, comments)
            print(f"    Saved {len(comments)} comments")
        else:
            print(f"    Could not fetch full info")

    print(f"Done: {poi_name}")

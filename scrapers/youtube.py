from typing import Optional
from datetime import datetime, timezone
import yt_dlp
from dotenv import load_dotenv
from db.client import get_client

load_dotenv()

QUERY_TEMPLATES = ["{} tips", "{} visit guide", "{} review", "{} itinerary"]
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


def _extract_transcript(info: dict) -> Optional[dict]:
    subs = info.get("subtitles", {}).get("en") or info.get("automatic_captions", {}).get("en")
    if not subs:
        return None
    return {"language": "en", "segments_json": subs, "full_text": None}


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

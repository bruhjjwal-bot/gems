"""Backfill missing YouTube transcripts via pintostudio/youtube-transcript-scraper.

Queries all youtube_videos with no youtube_transcripts row, fires one Apify
actor call per video (up to CONCURRENCY in parallel), transforms the output
{start, dur, text} segments to our {start_ms, duration_ms, text} schema, then
upserts into youtube_transcripts.

Usage:
    .venv/bin/python -u scripts/backfill_transcripts_apify.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from dotenv import load_dotenv
load_dotenv()

from apify_client import ApifyClient
from db.client import get_client

ACTOR_ID = "pintostudio/youtube-transcript-scraper"
CONCURRENCY = 8
RUN_TIMEOUT = timedelta(minutes=2)


def _fetch_missing_videos() -> list[dict]:
    db = get_client()
    rows = (
        db.rpc(
            "fetch_videos_missing_transcripts",
            {},
        ).execute().data
    )
    if rows is None:
        # Fallback: plain join via two queries
        all_videos = db.table("youtube_videos").select("id, video_id, title, poi_id").execute().data or []
        existing = {r["video_id"] for r in db.table("youtube_transcripts").select("video_id").execute().data or []}
        return [v for v in all_videos if v["id"] not in existing]
    return rows


def _fetch_missing_videos_direct() -> list[dict]:
    """Query videos with no transcript row via two round-trips (no RPC needed)."""
    db = get_client()
    all_videos = db.table("youtube_videos").select("id, video_id, title, poi_id").execute().data or []
    transcribed = {r["video_id"] for r in db.table("youtube_transcripts").select("video_id").execute().data or []}
    return [v for v in all_videos if v["id"] not in transcribed]


def _call_apify(video: dict, client: ApifyClient) -> tuple[dict, list[dict]]:
    """Call the transcript actor for one video. Returns (video, segments)."""
    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    try:
        run = client.actor(ACTOR_ID).call(
            run_input={"videoUrl": url, "targetLanguage": "en"},
            run_timeout=RUN_TIMEOUT,
        )
        if run is None:
            return video, []
        dataset_id = getattr(run, "default_dataset_id", None) or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
        if not dataset_id:
            return video, []
        items = list(client.dataset(dataset_id).iterate_items())
        # Actor returns one item: {"data": [{start, dur, text}, ...]}
        segments_raw = items[0].get("data", []) if items else []
        return video, segments_raw
    except Exception as e:
        print(f"  [ERROR] {video['title'][:60]}: {e}")
        return video, []


def _transform_segments(raw_items: list[dict]) -> tuple[list[dict], str]:
    """Convert Apify {start, dur, text} → our {start_ms, duration_ms, text} + full_text."""
    segments = []
    text_parts = []
    for item in raw_items:
        try:
            start_ms = int(float(item.get("start", 0)) * 1000)
            duration_ms = int(float(item.get("dur", 0)) * 1000)
            text = (item.get("text") or "").replace("\n", " ").strip()
        except (TypeError, ValueError):
            continue
        if not text:
            continue
        segments.append({"start_ms": start_ms, "duration_ms": duration_ms, "text": text})
        text_parts.append(text)
    return segments, " ".join(text_parts)


def _upsert_transcript(video_id_uuid: str, segments: list[dict], full_text: str):
    get_client().table("youtube_transcripts").upsert(
        {
            "video_id": video_id_uuid,
            "language": "en",
            "full_text": full_text,
            "segments_json": segments,
        },
        on_conflict="video_id",
    ).execute()


def main():
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN not set in .env")

    print("Querying videos missing transcripts...")
    videos = _fetch_missing_videos_direct()
    print(f"Found {len(videos)} videos without transcripts\n")

    if not videos:
        print("Nothing to do.")
        return

    client = ApifyClient(token)
    saved = 0
    empty = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(_call_apify, v, client): v for v in videos}
        for future in as_completed(futures):
            video, items = future.result()
            title = video["title"][:65]
            if not items:
                print(f"  [no transcript] {title}")
                empty += 1
                continue
            segments, full_text = _transform_segments(items)
            if not full_text:
                print(f"  [empty text]    {title}")
                empty += 1
                continue
            _upsert_transcript(video["id"], segments, full_text)
            print(f"  [saved]         {title}  ({len(segments)} segs, {len(full_text)} chars)")
            saved += 1

    print(f"\nDone. {saved} transcripts saved, {empty} videos have no transcript, {errors} errors.")


if __name__ == "__main__":
    main()

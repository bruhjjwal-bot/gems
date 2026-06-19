"""Transcript chunking: group segments_json into ~60-90s windows."""

CHUNK_TARGET_MS = 75_000  # ~75 seconds per chunk


def chunk_segments(segments: list[dict], target_ms: int = CHUNK_TARGET_MS) -> list[dict]:
    """Group consecutive segments into ~target_ms windows.

    Input: [{"start_ms": int, "duration_ms": int, "text": str}, ...]
    Output: [{"start_ms": int, "end_ms": int, "text": str}, ...]
    """
    if not segments:
        return []
    chunks: list[dict] = []
    current: list[dict] = []
    chunk_start = segments[0].get("start_ms", 0)

    for seg in segments:
        start = seg.get("start_ms", 0)
        if current and (start - chunk_start) >= target_ms:
            chunks.append(_finalise(current))
            current = []
            chunk_start = start
        current.append(seg)

    if current:
        chunks.append(_finalise(current))
    return chunks


def _finalise(segs: list[dict]) -> dict:
    start_ms = segs[0].get("start_ms", 0)
    last = segs[-1]
    end_ms = last.get("start_ms", 0) + last.get("duration_ms", 0)
    text = " ".join((s.get("text") or "").strip() for s in segs if s.get("text"))
    return {"start_ms": start_ms, "end_ms": end_ms, "text": text}

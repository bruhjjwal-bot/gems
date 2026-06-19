"""Enrich flat_insights_all.json with cluster joins + raw-data joins + LLM tags.

Inputs
------
- l2/data/flat_insights_all.json          (atomic insights, Louvre + Colosseum)
- l2/data/labelled_clusters_all.json      (cluster labels w/ member_indices)
- l2/data/raw_reviews.json                (TripAdvisor reviews: review_id, rating, visit_date, helpful_count)
- l2/data/raw_google_review.json          (Google reviews: rating, meta.lang)
- l2/data/raw_reddit_comment.json
- l2/data/raw_reddit_post.json
- l2/data/raw_youtube_comment.json
- l2/data/raw_youtube_transcript_chunk.json

Output
------
- l2/data/flat_insights_enriched.json     (same rows, schema extended)

Run
---
cd /Users/headout/Documents/Gems/gems-scraper && \
  set -a && source .env && set +a && \
  /Users/headout/Documents/Gems/gems-scraper/.venv/bin/python -u l2/enrich_insights.py
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Allow `python l2/enrich_insights.py` from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from l2.llm_client import call_json  # noqa: E402

DATA_DIR = REPO_ROOT / "l2" / "data"
FLAT_IN = DATA_DIR / "flat_insights_all.json"
CLUSTERS_IN = DATA_DIR / "labelled_clusters_all.json"
FLAT_OUT = DATA_DIR / "flat_insights_enriched.json"

RAW_FILES = {
    "tripadvisor_review": DATA_DIR / "raw_reviews.json",
    "google_review": DATA_DIR / "raw_google_review.json",
    "reddit_comment": DATA_DIR / "raw_reddit_comment.json",
    "reddit_post": DATA_DIR / "raw_reddit_post.json",
    "youtube_comment": DATA_DIR / "raw_youtube_comment.json",
    "youtube_transcript_chunk": DATA_DIR / "raw_youtube_transcript_chunk.json",
}

BATCH_SIZE = 10
MAX_WORKERS = 50
MODEL = "gpt-4o"
TEMPERATURE = 0.1
MAX_TOKENS = 2000

# Freshness decay constant: 2 years.
TAU_SECONDS = 2 * 365.25 * 24 * 3600

# Try to load langdetect; fall back gracefully.
try:
    from langdetect import DetectorFactory, detect  # type: ignore

    DetectorFactory.seed = 0
    _HAS_LANGDETECT = True
except Exception:
    _HAS_LANGDETECT = False


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def detect_lang(text: str, fallback_meta_lang: Optional[str] = None) -> str:
    """Best-effort language detection."""
    if fallback_meta_lang:
        return fallback_meta_lang
    if not text or not text.strip():
        return "en"
    if _HAS_LANGDETECT:
        try:
            return detect(text[:1000])
        except Exception:
            return "en"
    return "en"


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_visit_date(s: Optional[str]) -> Optional[str]:
    """TripAdvisor visit_date is e.g. 'Jun 2026' -> '2026-06-01'."""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"([A-Za-z]{3,9})\s+(\d{4})", s.strip())
    if not m:
        return None
    mon_name = m.group(1)[:3].lower()
    year = int(m.group(2))
    month = _MONTH_MAP.get(mon_name)
    if month is None:
        return None
    return f"{year:04d}-{month:02d}-01"


def freshness_score(created_at: Optional[str]) -> float:
    """Exponential decay of (now - created_at) with tau=2y. Defaults to 0.5."""
    if not created_at:
        return 0.5
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return 0.5
    now = datetime.now(timezone.utc)
    age = (now - dt).total_seconds()
    if age <= 0:
        return 1.0
    return float(math.exp(-age / TAU_SECONDS))


def load_json(path: Path) -> Any:
    with path.open() as f:
        return json.load(f)


# -----------------------------------------------------------------------------
# LLM batched tagging
# -----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a precise structured-data extractor for travel-POI insights.

For each insight you receive, return a JSON object with EXACTLY these keys:
- insight_sentiment: one of "positive", "negative", "neutral", "mixed"
- intent_tag: one of "warning", "recommendation", "explanation", "comparison", "history", "description", "other"
- numeric_anchors: list of verbatim numeric anchors found in the text. Include prices ("€17", "$25"), durations ("3 hours", "90 minutes"), times of day ("10am", "7:30pm"), counts ("2 tickets"), distances ("500 metres"), etc. Empty list if none.
- sub_attraction: a specific named sub-attraction, room, ticket, service, or operator inside / adjacent to the POI (e.g. "Denon Wing", "Mona Lisa room", "Roma Pass", "Arena ticket", "Palatine Hill"). Null if no specific named sub-attraction.
- visit_date_mentioned: any visit timeframe parsed from the text itself (e.g. "June 2024", "summer 2023", "last winter"). Null if none.

Return STRICTLY valid JSON. No prose.
"""

USER_TEMPLATE = """POI: {poi_name}

Insights (one per line, prefixed by id):
{lines}

Respond with a JSON object of the shape:
{{
  "results": [
    {{"id": 0, "insight_sentiment": "...", "intent_tag": "...", "numeric_anchors": [...], "sub_attraction": null, "visit_date_mentioned": null}},
    ...
  ]
}}

Return EXACTLY {n} objects in "results", one per id 0..{n_minus_1}, in order.
"""


VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}
VALID_INTENTS = {
    "warning", "recommendation", "explanation",
    "comparison", "history", "description", "other",
}


def _coerce_result(raw: Any) -> dict:
    """Normalise a single LLM result object; clamp to valid enums."""
    if not isinstance(raw, dict):
        raw = {}
    sent = raw.get("insight_sentiment")
    if sent not in VALID_SENTIMENTS:
        sent = "neutral"
    intent = raw.get("intent_tag")
    if intent not in VALID_INTENTS:
        intent = "other"
    anchors = raw.get("numeric_anchors") or []
    if not isinstance(anchors, list):
        anchors = []
    anchors = [str(a) for a in anchors if a]
    sub = raw.get("sub_attraction")
    if isinstance(sub, str) and sub.strip().lower() in {"", "null", "none"}:
        sub = None
    vdm = raw.get("visit_date_mentioned")
    if isinstance(vdm, str) and vdm.strip().lower() in {"", "null", "none"}:
        vdm = None
    return {
        "insight_sentiment": sent,
        "intent_tag": intent,
        "numeric_anchors": anchors,
        "sub_attraction": sub,
        "visit_date_mentioned": vdm,
    }


def _empty_tag() -> dict:
    return {
        "insight_sentiment": "neutral",
        "intent_tag": "other",
        "numeric_anchors": [],
        "sub_attraction": None,
        "visit_date_mentioned": None,
    }


def tag_batch(batch: list[dict]) -> list[dict]:
    """Send a batch of insights to the LLM and return one tag dict per item.

    Each `batch` entry is expected to carry:
      - poi_name (str)
      - cluster_name (str | None)
      - text (str)
    """
    n = len(batch)
    if n == 0:
        return []
    lines = []
    for i, item in enumerate(batch):
        cname = item.get("cluster_name") or "(no cluster)"
        # Keep each line compact; trim very long text to ~700 chars to bound tokens.
        text = (item.get("text") or "").replace("\n", " ").strip()
        if len(text) > 700:
            text = text[:700] + "…"
        lines.append(f"[{i}] cluster=\"{cname}\" :: {text}")
    poi_names = sorted({item.get("poi_name", "") for item in batch})
    poi_str = ", ".join(poi_names) if poi_names else "Unknown"
    user = USER_TEMPLATE.format(
        poi_name=poi_str,
        lines="\n".join(lines),
        n=n,
        n_minus_1=n - 1,
    )
    try:
        resp = call_json(
            system=SYSTEM_PROMPT,
            user=user,
            model=MODEL,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as e:  # call_json already retries 3x
        print(f"[warn] LLM batch failed after retries ({e}); using empty tags", flush=True)
        return [_empty_tag() for _ in range(n)]

    results = resp.get("results") if isinstance(resp, dict) else None
    if not isinstance(results, list):
        return [_empty_tag() for _ in range(n)]

    # Map by id when possible, else by index.
    by_id: dict[int, dict] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        try:
            rid = int(r.get("id"))
        except Exception:
            continue
        by_id[rid] = r
    out: list[dict] = []
    for i in range(n):
        if i in by_id:
            out.append(_coerce_result(by_id[i]))
        elif i < len(results):
            out.append(_coerce_result(results[i]))
        else:
            out.append(_empty_tag())
    return out


# -----------------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------------

def build_raw_lookup() -> dict[tuple[str, str], dict]:
    """source-name + source_id -> raw row.

    TripAdvisor raw uses `review_id` so we key on that. All other raw files
    already have `source_id`.
    """
    lookup: dict[tuple[str, str], dict] = {}
    for source_name, path in RAW_FILES.items():
        if not path.exists():
            print(f"[warn] missing raw file: {path}", flush=True)
            continue
        rows = load_json(path)
        if source_name == "tripadvisor_review":
            for r in rows:
                sid = r.get("review_id")
                if sid:
                    lookup[(source_name, str(sid))] = r
        else:
            for r in rows:
                sid = r.get("source_id")
                if sid:
                    lookup[(source_name, str(sid))] = r
        print(f"  loaded {len(rows):>5} from {path.name}", flush=True)
    return lookup


def build_cluster_lookup(clusters: list[dict]) -> dict[int, dict]:
    """Insight row index -> cluster dict (first cluster wins on ties)."""
    rev: dict[int, dict] = {}
    for c in clusters:
        for idx in c.get("member_indices", []):
            if idx not in rev:
                rev[idx] = c
    return rev


def attach_mechanical(
    row: dict,
    flat_index: int,
    raw_lookup: dict[tuple[str, str], dict],
    cluster_lookup: dict[int, dict],
) -> dict:
    """Attach all non-LLM fields. Mutates `row` in place and returns it."""
    source = row.get("source") or ""
    source_id = str(row.get("source_id") or "")
    raw = raw_lookup.get((source, source_id))

    # rating: prefer flat_insights row (already populated for TA); fall back to raw.
    rating = row.get("rating")
    if rating is None and raw is not None:
        rating = raw.get("rating")

    helpful_count: Optional[int] = None
    created_at: Optional[str] = None
    meta_lang: Optional[str] = None

    if raw is not None:
        if source == "tripadvisor_review":
            helpful_count = raw.get("helpful_count")
            created_at = parse_visit_date(raw.get("visit_date"))
        elif source == "google_review":
            meta_lang = (raw.get("meta") or {}).get("lang")
        # Other sources have no reliable timestamp in current raw schema.

    language = detect_lang(row.get("text") or "", fallback_meta_lang=meta_lang)

    cluster = cluster_lookup.get(flat_index)
    if cluster is None:
        cluster_fields = {
            "cluster_id": None,
            "l1": None,
            "l2": None,
            "l3": None,
            "secondary_l1": None,
            "cluster_quality_tier": None,
            "cluster_sentiment": None,
            "cluster_size": None,
            "cluster_n_sources": None,
            "cluster_name": None,
        }
    else:
        cluster_fields = {
            "cluster_id": cluster.get("cluster_id"),
            "l1": cluster.get("l1"),
            "l2": cluster.get("l2"),
            "l3": cluster.get("l3"),
            "secondary_l1": cluster.get("secondary_l1"),
            "cluster_quality_tier": cluster.get("quality_tier"),
            "cluster_sentiment": cluster.get("sentiment"),
            "cluster_size": cluster.get("size"),
            "cluster_n_sources": cluster.get("n_sources"),
            "cluster_name": cluster.get("name"),
        }

    row.update(cluster_fields)
    row["rating"] = rating
    row["helpful_count"] = helpful_count
    row["created_at"] = created_at
    row["language"] = language
    row["freshness_score"] = freshness_score(created_at)
    return row


def chunked(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield i, seq[i : i + n]


def main() -> None:
    t_start = time.time()
    print(f"Loading flat insights from {FLAT_IN} …", flush=True)
    flat = load_json(FLAT_IN)
    print(f"  {len(flat)} insights", flush=True)

    print(f"Loading clusters from {CLUSTERS_IN} …", flush=True)
    clusters = load_json(CLUSTERS_IN)
    print(f"  {len(clusters)} clusters", flush=True)

    print("Loading raw source files …", flush=True)
    raw_lookup = build_raw_lookup()
    print(f"  raw lookup size: {len(raw_lookup)}", flush=True)

    print("Building cluster reverse map …", flush=True)
    cluster_lookup = build_cluster_lookup(clusters)
    print(
        f"  {len(cluster_lookup)} of {len(flat)} insights belong to a cluster",
        flush=True,
    )

    print("Attaching mechanical fields …", flush=True)
    for i, row in enumerate(flat):
        attach_mechanical(row, i, raw_lookup, cluster_lookup)
    print("  mechanical join complete", flush=True)

    # ------------------------------------------------------------------
    # LLM batched tagging
    # ------------------------------------------------------------------
    batches: list[tuple[int, list[int]]] = []  # (batch_id, [flat_idx, ...])
    indices = list(range(len(flat)))
    for batch_id, (start, _) in enumerate(chunked(indices, BATCH_SIZE)):
        batches.append((batch_id, indices[start : start + BATCH_SIZE]))
    total_batches = len(batches)
    print(
        f"Submitting {total_batches} LLM batches (size={BATCH_SIZE}, workers={MAX_WORKERS}, model={MODEL}) …",
        flush=True,
    )

    # Pre-allocate slot for each insight.
    tags: list[Optional[dict]] = [None] * len(flat)

    def _work(batch_id: int, idxs: list[int]) -> tuple[int, list[int], list[dict]]:
        batch_payload = [
            {
                "poi_name": flat[i].get("poi_name"),
                "cluster_name": flat[i].get("cluster_name"),
                "text": flat[i].get("text"),
            }
            for i in idxs
        ]
        return batch_id, idxs, tag_batch(batch_payload)

    completed = 0
    t_llm_start = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_work, bid, idxs) for bid, idxs in batches]
        for fut in as_completed(futures):
            try:
                _bid, idxs, results = fut.result()
            except Exception as e:
                print(f"[warn] batch crashed: {e}", flush=True)
                continue
            for idx, tag in zip(idxs, results):
                tags[idx] = tag
            completed += 1
            if completed % 50 == 0 or completed == total_batches:
                elapsed = time.time() - t_llm_start
                rate = completed / max(elapsed, 1e-6)
                eta = (total_batches - completed) / max(rate, 1e-6)
                print(
                    f"  [{completed:>4}/{total_batches}] elapsed={elapsed:6.1f}s "
                    f"rate={rate:5.2f} b/s eta={eta:6.1f}s",
                    flush=True,
                )

    # Merge tags onto rows; backfill any missing with empty tag.
    print("Merging LLM tags …", flush=True)
    for i, row in enumerate(flat):
        tag = tags[i] or _empty_tag()
        row.update(tag)

    # Strip the transient cluster_name field — it lived only as LLM context.
    # (Keep it: it's actually useful downstream. We'll keep it.)

    print(f"Writing {FLAT_OUT} …", flush=True)
    with FLAT_OUT.open("w") as f:
        json.dump(flat, f, ensure_ascii=False)
    print(
        f"Done in {time.time() - t_start:.1f}s — {len(flat)} rows written to {FLAT_OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()

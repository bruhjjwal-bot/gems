"""Local HTTP wrapper around ask_gems for UI consumption.

A thin Flask layer over the same retrieval/evidence functions the MCP server
exposes, plus the ask_gems agent loop. Designed for a teammate to point a
browser-side UI at without any MCP plumbing.

Run:
    .venv/bin/python -m l2.api_server                       # localhost:8000
    PORT=8080 .venv/bin/python -m l2.api_server             # custom port

Endpoints:
    POST /api/ask                — LLM-synthesised RAG answer (ask_gems)
    POST /api/search_clusters    — tier-1 raw retrieval (no LLM)
    POST /api/search_insights    — tier-2 raw retrieval (no LLM)
    POST /api/explain_cluster    — cluster card + raw quotes
    GET  /api/pois               — list POIs + summary metadata
    GET  /api/health             — liveness + store stats

CORS is wide-open for local dev (any origin, GET+POST).
"""
import json
import logging
import os
import re
import sys
import traceback
from collections import Counter
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

from l2.ask_gems import ask as _ask
from l2.mcp_server.evidence import explain as _explain
from l2.mcp_server.retrieval import (
    list_concerns as _list_concerns,
    list_highlights as _list_highlights,
    search as _search,
    search_insights as _search_insights,
)
from l2.mcp_server.store import load_all

log = logging.getLogger("gems.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"]}})


_HERE = Path(__file__).resolve().parent
PUBLIC_DIR = (_HERE.parent / "public").resolve()


@app.route("/")
def home():
    """Editorial landing page listing the demo apps."""
    index_path = PUBLIC_DIR / "index.html"
    if index_path.is_file():
        return send_from_directory(PUBLIC_DIR, "index.html")
    # Fall back to the original chat UI if the landing page isn't present.
    return send_from_directory(_HERE, "chat.html")


@app.route("/chat")
def chat_ui():
    """Legacy chat UI kept reachable at /chat for backwards compatibility."""
    return send_from_directory(_HERE, "chat.html")


@app.route("/<path:filename>")
def static_files(filename: str):
    """Serve files from /public, with .html resolution. Never shadows /api/*."""
    if filename.startswith("api/"):
        abort(404)
    # Try the exact path, then .html-resolved path.
    for candidate in (filename, f"{filename}.html"):
        full = (PUBLIC_DIR / candidate).resolve()
        # Path-traversal guard: stay inside PUBLIC_DIR.
        try:
            full.relative_to(PUBLIC_DIR)
        except ValueError:
            continue
        if full.is_file():
            return send_from_directory(PUBLIC_DIR, candidate)
    abort(404)


def _bad(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _enrich_citation_urls(payload: dict) -> None:
    """Back-fill source_url on any citations the LLM left blank.

    The LLM picks citations from explain_cluster output which now carries
    source_url. But if it omits the field, we resolve it here from the
    in-memory raw store so every citation has a clickable link where possible.
    """
    cites = payload.get("citations") or []
    if not cites:
        return
    s = load_all()
    for c in cites:
        if c.get("source_url"):
            continue
        source = c.get("source")
        source_id = c.get("source_id")
        if not source or not source_id:
            continue
        raw_row = (s.raw.get(source) or {}).get(source_id)
        if raw_row:
            c["source_url"] = raw_row.get("source_url")


def _decycle(obj, seen=None):
    """Break self-references before serialisation. ask_gems' final_payload
    aliases into tool_calls_log[-1]['args'] (same dict), which crashes json.dumps."""
    if seen is None:
        seen = set()
    if isinstance(obj, dict):
        oid = id(obj)
        if oid in seen:
            return "<cycle>"
        seen.add(oid)
        return {k: _decycle(v, seen) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        oid = id(obj)
        if oid in seen:
            return "<cycle>"
        seen.add(oid)
        return [_decycle(v, seen) for v in obj]
    return obj


def _safe_json(payload):
    """Round-trip through json.dumps(default=str) after decycling so numpy /
    non-JSON types don't blow up Flask's jsonify."""
    return app.response_class(
        json.dumps(_decycle(payload), default=str),
        mimetype="application/json",
    )


def _wrap(fn):
    """Convert exceptions into JSON 400/500 instead of HTML stack traces."""
    def inner(*a, **kw):
        try:
            return fn(*a, **kw)
        except (KeyError, ValueError, TypeError) as e:
            log.warning("bad request to %s: %s", fn.__name__, e)
            return _bad(str(e), 400)
        except Exception as e:
            log.error("internal error in %s: %s\n%s", fn.__name__, e, traceback.format_exc())
            return _bad(f"internal error: {e}", 500)
    inner.__name__ = fn.__name__
    return inner


# ---- Source URL enrichment ------------------------------------------------

_URL_FIELD = {
    "reddit_post": "post_url",
    "reddit_comment": "comment_url",
    "youtube_transcript_chunk": "url",
}

_PLATFORM_LABEL = {
    "reddit_post": "Reddit Post",
    "reddit_comment": "Reddit Comment",
    "youtube_transcript_chunk": "YouTube",
    "tripadvisor_review": "TripAdvisor",
    "google_review": "Google Reviews",
}


def _attach_source_urls(citations: list) -> list:
    """Enrich each citation with a source URL and human-readable platform label.

    Looks up the raw row from the in-memory store (no extra DB round-trip).
    TripAdvisor and Google Reviews have no per-review URLs, so url=None for those.
    """
    if not citations:
        return citations
    s = load_all()
    result = []
    for c in citations:
        source = c.get("source", "")
        source_id = str(c.get("source_id", ""))
        raw_row = s.raw.get(source, {}).get(source_id)
        url = None
        if raw_row:
            field = _URL_FIELD.get(source)
            if field:
                url = raw_row.get(field)
        result.append({
            **c,
            "url": url,
            "platform_label": _PLATFORM_LABEL.get(source, source),
        })
    return result


# ---- Routes ---------------------------------------------------------------


@app.route("/api/health", methods=["GET"])
@_wrap
def health():
    s = load_all()
    return jsonify({
        "status": "ok",
        "clusters": len(s.clusters),
        "insights": len(s.insights),
        "pois": s.pois,
        "last_built": s.last_built_at,
    })


@app.route("/api/pois", methods=["GET"])
@_wrap
def pois():
    s = load_all()
    out = []
    for poi in s.pois:
        poi_clusters = [c for c in s.clusters if c["poi_name"] == poi]
        sources: set = set()
        total_insights = 0
        tier_a = 0
        for c in poi_clusters:
            total_insights += int(c.get("size", 0))
            if c.get("quality_tier") == "A":
                tier_a += 1
            sources.update((c.get("source_mix") or {}).keys())
        out.append({
            "poi_name": poi,
            "total_clusters": len(poi_clusters),
            "total_insights": total_insights,
            "tier_a_count": tier_a,
            "sources": sorted(sources),
            "last_built": s.last_built_at,
        })
    return jsonify(out)


@app.route("/api/ask", methods=["POST"])
@_wrap
def ask():
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return _bad("'query' is required")
    poi = body.get("poi")
    booking_context = body.get("booking_context")
    if booking_context:
        query = f"[booking context: {booking_context}]\n{query}"
    payload = _ask(query, poi_hint=poi, verbose=False)
    _enrich_citation_urls(payload)
    return _safe_json(payload)


@app.route("/api/search_clusters", methods=["POST"])
@_wrap
def search_clusters():
    body = request.get_json(silent=True) or {}
    poi = body.get("poi")
    query = body.get("query")
    if not poi or not query:
        return _bad("'poi' and 'query' are required")
    rows = _search(
        poi=poi,
        query=query,
        intent_l1=body.get("intent_l1"),
        limit=int(body.get("limit", 5)),
        min_tier=body.get("min_tier", "B"),
    )
    return jsonify(rows)


@app.route("/api/search_insights", methods=["POST"])
@_wrap
def search_insights():
    body = request.get_json(silent=True) or {}
    poi = body.get("poi")
    query = body.get("query")
    if not poi or not query:
        return _bad("'poi' and 'query' are required")
    rows = _search_insights(
        poi=poi,
        query=query,
        filters=body.get("filters") or {},
        limit=int(body.get("limit", 10)),
    )
    return jsonify(rows)


@app.route("/api/explain_cluster", methods=["POST"])
@_wrap
def explain_cluster():
    body = request.get_json(silent=True) or {}
    cid = body.get("cluster_id")
    if cid is None:
        return _bad("'cluster_id' is required")
    return jsonify(_explain(cluster_id=int(cid), max_quotes=int(body.get("max_quotes", 5))))


# ---- Aggregate rollups ----------------------------------------------------

_L1_BUCKETS = (
    "Visit Intelligence",
    "Attention Intelligence",
    "Discovery Intelligence",
    "Culinary Intelligence",
    "Operational Intelligence",
)
_SOURCE_BUCKETS = (
    "tripadvisor_review",
    "google_review",
    "reddit_post",
    "reddit_comment",
    "youtube_transcript_chunk",
)
_SENTIMENT_BUCKETS = ("positive", "negative", "neutral", "mixed")
_INTENT_BUCKETS = (
    "warning",
    "recommendation",
    "explanation",
    "comparison",
    "history",
    "description",
)
_TIER_BUCKETS = ("A", "B", "C")

_TIME_UNIT_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(min(?:ute)?s?|hours?|hrs?)\b", re.I)


def _zero_counts(keys):
    return {k: 0 for k in keys}


def _extract_minutes(anchor: str):
    """Pull a minute count out of free-text anchors like '3 hours', '90 mins'.

    Returns None if no time anchor present.
    """
    if not isinstance(anchor, str):
        return None
    m = _TIME_UNIT_RE.search(anchor)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    unit = m.group(2).lower()
    if unit.startswith("h"):
        return value * 60.0
    return value


@app.route("/api/poi_stats", methods=["GET"])
@_wrap
def poi_stats():
    """Per-POI aggregate rollup. Pure counting over the warm store, no LLM."""
    poi = (request.args.get("poi") or "").strip()
    if not poi:
        return _bad("'poi' query param is required")
    s = load_all()
    if poi not in s.pois:
        return _bad(f"unknown poi: {poi!r}. Supported: {s.pois}", 404)

    clusters = [c for c in s.clusters if c.get("poi_name") == poi]
    insights = [i for i in s.insights if i.get("poi_name") == poi]

    tier_counts = _zero_counts(_TIER_BUCKETS)
    for c in clusters:
        t = c.get("quality_tier")
        if t in tier_counts:
            tier_counts[t] += 1

    sentiment_counts = _zero_counts(_SENTIMENT_BUCKETS)
    intent_counts = _zero_counts(_INTENT_BUCKETS)
    l1_counts = _zero_counts(_L1_BUCKETS)
    source_counts = _zero_counts(_SOURCE_BUCKETS)
    for i in insights:
        sent = i.get("insight_sentiment")
        if sent in sentiment_counts:
            sentiment_counts[sent] += 1
        tag = i.get("intent_tag")
        if tag in intent_counts:
            intent_counts[tag] += 1
        l1 = i.get("l1")
        if l1 in l1_counts:
            l1_counts[l1] += 1
        src = i.get("source")
        if src in source_counts:
            source_counts[src] += 1

    total_clusters = len(clusters)
    total_insights = len(insights)
    tier_a_ratio = (tier_counts["A"] / total_clusters) if total_clusters else 0.0
    positive_share = (
        sentiment_counts["positive"] / total_insights if total_insights else 0.0
    )
    warning_share = (
        intent_counts["warning"] / total_insights if total_insights else 0.0
    )
    raw_score = 100 * (0.5 * tier_a_ratio + 0.3 * positive_share + 0.2 * (1 - warning_share))
    verdict_score = max(0, min(100, round(raw_score)))

    return jsonify({
        "poi": poi,
        "total_clusters": total_clusters,
        "total_insights": total_insights,
        "tier_counts": tier_counts,
        "sentiment_counts": sentiment_counts,
        "intent_tag_counts": intent_counts,
        "l1_counts": l1_counts,
        "source_counts": source_counts,
        "verdict_score": verdict_score,
    })


@app.route("/api/sub_attractions", methods=["GET"])
@_wrap
def sub_attractions():
    """Per-POI rollup of insights grouped by sub_attraction."""
    poi = (request.args.get("poi") or "").strip()
    if not poi:
        return _bad("'poi' query param is required")
    try:
        min_mentions = int(request.args.get("min_mentions", "2"))
    except ValueError:
        return _bad("'min_mentions' must be an integer")

    s = load_all()
    if poi not in s.pois:
        return _bad(f"unknown poi: {poi!r}. Supported: {s.pois}", 404)

    groups: dict[str, dict] = {}
    for row in s.insights:
        if row.get("poi_name") != poi:
            continue
        sub = (row.get("sub_attraction") or "").strip()
        if not sub:
            continue
        g = groups.setdefault(sub, {
            "sub_attraction": sub,
            "n_insights": 0,
            "sources": set(),
            "sentiment": Counter(),
            "intent": Counter(),
            "cluster_ids": Counter(),
            "minutes": [],
        })
        g["n_insights"] += 1
        if row.get("source"):
            g["sources"].add(row["source"])
        sent = row.get("insight_sentiment")
        if sent:
            g["sentiment"][sent] += 1
        tag = row.get("intent_tag")
        if tag:
            g["intent"][tag] += 1
        cid = row.get("cluster_id")
        if cid is not None:
            g["cluster_ids"][cid] += 1
        for anchor in row.get("numeric_anchors") or []:
            mins = _extract_minutes(anchor)
            if mins is not None:
                g["minutes"].append(mins)

    out = []
    for g in groups.values():
        if g["n_insights"] < min_mentions:
            continue
        avg_minutes = median(g["minutes"]) if g["minutes"] else None
        top_clusters = [cid for cid, _ in g["cluster_ids"].most_common(3)]
        top_intents = dict(g["intent"].most_common(5))
        out.append({
            "sub_attraction": g["sub_attraction"],
            "n_insights": g["n_insights"],
            "n_sources": len(g["sources"]),
            "positive_count": g["sentiment"].get("positive", 0),
            "negative_count": g["sentiment"].get("negative", 0),
            "warning_count": g["intent"].get("warning", 0),
            "recommendation_count": g["intent"].get("recommendation", 0),
            "avg_minutes": avg_minutes,
            "sample_cluster_ids": top_clusters,
            "top_intent_tags": top_intents,
        })
    out.sort(key=lambda r: r["n_insights"], reverse=True)
    return jsonify(out)


def main():
    log.info("warming store…")
    s = load_all()
    log.info("store ready: %d clusters, %d insights, pois=%s", len(s.clusters), len(s.insights), s.pois)
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

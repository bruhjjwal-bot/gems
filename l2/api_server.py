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
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, send_from_directory
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


@app.route("/")
def chat_ui():
    return send_from_directory(_HERE, "chat.html")


def _bad(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


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


def main():
    log.info("warming store…")
    s = load_all()
    log.info("store ready: %d clusters, %d insights, pois=%s", len(s.clusters), len(s.insights), s.pois)
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

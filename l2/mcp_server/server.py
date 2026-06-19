"""Gems POI Intelligence MCP server.

Exposes 5 read-only tools for per-POI insight retrieval, backed by labelled
clusters from the L2 pipeline. Stdio transport for local + Claude Code dev.

Register:
  claude mcp add gems-poi --stdio \
    "/path/to/.venv/bin/python -m l2.mcp_server.server"
"""
import os
import sys
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP

from . import config
from .evidence import explain as _explain
from .retrieval import list_concerns as _list_concerns
from .retrieval import list_highlights as _list_highlights
from .retrieval import search as _search
from .retrieval import search_insights as _search_insights
from .store import load_all

mcp = FastMCP("gems-poi")


@mcp.tool()
def list_pois() -> list[dict]:
    """List POIs this server has intelligence for. Returns name, totals, sources, last_built_at."""
    s = load_all()
    summaries: list[dict] = []
    for poi in s.pois:
        poi_clusters = [c for c in s.clusters if c["poi_name"] == poi]
        sources: set[str] = set()
        total_insights = 0
        tier_a = 0
        poi_id = ""
        for c in poi_clusters:
            poi_id = c.get("poi_id", poi_id)
            total_insights += int(c.get("size", 0))
            if c.get("quality_tier") == "A":
                tier_a += 1
            sources.update((c.get("source_mix") or {}).keys())
        summaries.append({
            "poi_id": poi_id,
            "poi_name": poi,
            "total_clusters": len(poi_clusters),
            "total_insights": total_insights,
            "sources": sorted(sources),
            "tier_a_count": tier_a,
            "last_built_at": s.last_built_at or "",
        })
    return summaries


@mcp.tool()
def search_poi_advice(
    poi: Literal["Louvre Museum", "Colosseum"],
    query: str,
    intent_l1: Optional[Literal[
        "Visit Intelligence",
        "Attention Intelligence",
        "Discovery Intelligence",
        "Culinary Intelligence",
        "Operational Intelligence",
    ]] = None,
    limit: int = 5,
    min_tier: Literal["A", "B", "C"] = "B",
) -> list[dict]:
    """Semantic search over POI clusters. Returns ranked cluster cards.

    Use intent_l1 to bias toward a category (Visit/Attention/Discovery/Culinary/Operational).
    min_tier defaults to B+ to filter out generic-noise clusters.
    Call explain_cluster(cluster_id) on results to get the raw evidence quotes.
    """
    return _search(poi=poi, query=query, intent_l1=intent_l1, limit=limit, min_tier=min_tier)


@mcp.tool()
def list_poi_concerns(
    poi: Literal["Louvre Museum", "Colosseum"],
    category: Optional[Literal[
        "Pricing",
        "Policy & Booking",
        "Facilities",
        "Visitor Complaint",
        "Access Issue",
        "Third-party Operator",
    ]] = None,
    limit: int = 10,
) -> list[dict]:
    """Top operational complaints / things to avoid for the POI.

    Filters to Operational Intelligence + negative sentiment, sorted by corroboration
    (size × source diversity). Use category to drill into a specific concern L2.
    """
    return _list_concerns(poi=poi, category=category, limit=limit)


@mcp.tool()
def list_poi_highlights(
    poi: Literal["Louvre Museum", "Colosseum"],
    intent: Optional[Literal[
        "Visit Intelligence",
        "Attention Intelligence",
        "Discovery Intelligence",
        "Culinary Intelligence",
    ]] = None,
    limit: int = 10,
) -> list[dict]:
    """Top recommendations / things to do for the POI.

    Filters to Tier A + positive sentiment. Use intent to drill into a category
    (Attention = artwork/highlights, Discovery = nearby, Culinary = eat/drink, Visit = planning).
    """
    return _list_highlights(poi=poi, intent=intent, limit=limit)


@mcp.tool()
def search_insights(
    poi: Literal["Louvre Museum", "Colosseum"],
    query: str,
    filters: Optional[dict] = None,
    limit: int = 10,
) -> list[dict]:
    """Tier-2 retrieval: semantic search over per-insight rows (granular, not clustered).

    Use when you need exact quotes / numeric anchors / specific sub-attraction
    mentions that get averaged out at the cluster level. Returns insight cards
    joined with their parent cluster name.

    filters (all optional, AND-combined):
      - l1: str — exact L1 taxonomy match
      - l2: str — exact L2 taxonomy match
      - rating_min / rating_max: int (1-5)
      - source: list[str] — e.g. ["reddit_post", "reddit_comment"]
      - insight_sentiment: list[str] — e.g. ["negative"]
      - intent_tag: list[str] — e.g. ["warning", "recommendation"]
      - has_numeric: bool — only rows with numeric_anchors populated
      - sub_attraction: str — case-insensitive substring match (e.g. "Denon")
      - cluster_id: int
      - min_freshness: float (0..1)
    """
    return _search_insights(poi=poi, query=query, filters=filters, limit=limit)


@mcp.tool()
def explain_cluster(cluster_id: int, max_quotes: int = 5) -> dict:
    """Return the full evidence trail for a cluster — the actual reviewer quotes.

    This is the trust layer: every agent claim built on a cluster card should cite
    one or more of these raw quotes (with source + source_id) so the user can verify.
    """
    return _explain(cluster_id=cluster_id, max_quotes=max_quotes)


def main():
    # Eager-load on startup so first request isn't slow
    s = load_all()
    print(
        f"[gems-poi] loaded {len(s.clusters)} clusters, {len(s.insights)} insights, "
        f"raw sources={list(s.raw.keys())}, pois={s.pois}, last_built={s.last_built_at}",
        file=sys.stderr,
    )
    if s.embeddings is None:
        print(
            "[gems-poi] WARNING: cluster_embeddings.npy missing. search_poi_advice will fail. "
            "Run `.venv/bin/python -u l2/build_index.py`.",
            file=sys.stderr,
        )
    if s.insight_embeddings is None:
        print(
            "[gems-poi] WARNING: insight_embeddings.npy missing. search_insights will fail. "
            "Build the per-insight embeddings to enable tier-2 retrieval.",
            file=sys.stderr,
        )
    else:
        print(
            f"[gems-poi] insight embeddings: {s.insight_embeddings.shape[0]} rows loaded",
            file=sys.stderr,
        )
    mcp.run()


if __name__ == "__main__":
    main()

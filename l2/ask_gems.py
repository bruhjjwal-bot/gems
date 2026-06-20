"""ask_gems — the LLM consumer that wraps the Gems MCP tools to answer
free-text traveler questions with cited evidence.

Architecture: a thin agent using OpenAI gpt-4o function-calling. Tools mirror
the MCP surface 1:1, but we import them directly to skip the stdio protocol
(hackathon speed). Loop runs ≤6 tool calls then must emit a final answer.

Hard rules enforced via system prompt + output validation:
  - The agent NEVER answers from memory — must call retrieval tools first.
  - Every claim must cite ≥2 evidence quotes (source, source_id, snippet).
  - If evidence is thin, the agent must say so explicitly with confidence=low.

Usage:
    .venv/bin/python l2/ask_gems.py "Is the Mona Lisa worth seeing?"
    .venv/bin/python l2/ask_gems.py --poi "Colosseum" "Are there scams to avoid?"
    .venv/bin/python l2/ask_gems.py --demo   # runs 5 sample queries
    .venv/bin/python l2/ask_gems.py --json "Should I get the Roma Pass?"  # JSON only

Environment: OPENAI_API_KEY in .env. POIs restricted to Louvre Museum + Colosseum.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

from l2.mcp_server.evidence import explain as _explain
from l2.mcp_server.retrieval import (
    list_concerns as _list_concerns,
    list_highlights as _list_highlights,
    search as _search,
    search_insights as _search_insights,
)
from l2.mcp_server.store import load_all

MODEL = "gpt-4o"
MAX_TOOL_ITERATIONS = 6


SYSTEM_PROMPT = """You are Gems — the POI Intelligence agent for traveler advice.

You answer questions about EXACTLY two POIs:
  - Louvre Museum (Paris)
  - Colosseum (Rome)

Refuse politely if asked about any other POI; your knowledge base only covers these two.

CRITICAL RULES (non-negotiable):
1. NEVER answer from memory or general knowledge. You MUST call retrieval tools
   first and base every claim on real cluster cards or insight rows.
2. Every claim you make MUST be backed by at least 2 evidence quotes from
   explain_cluster. Single-source claims are NOT acceptable.
3. If evidence is thin (< 2 quotes per claim), say so and set confidence: "low".
4. Be concise. Surface what the data says, not your commentary. No fluff.
5. ALWAYS attribute sources — which platforms corroborated (e.g. "TripAdvisor + Reddit + YouTube").
6. NEVER fabricate quotes, source_ids, or cluster_ids. Only use what tools return.

Workflow:
  STEP 1 — Pick the right tool for the query shape:
    * Broad/categorical ("what should I know about X?", "is X worth it?")
      → search_poi_advice (cluster-level, intent-routed)
    * Top concerns / things to avoid → list_poi_concerns
    * Top highlights / things to do → list_poi_highlights
    * Granular / filtered ("low-rating reviews about X", "specific prices",
      "warnings from Reddit") → search_insights with structured filters
  STEP 2 — Call explain_cluster on top 1-2 results to get the actual quotes.
  STEP 3 — Synthesize a 2-4 sentence answer citing the quotes.

End by calling `finalize_answer` with the structured payload. Do not emit a
plain text answer outside of finalize_answer.

Available POI taxonomy (L1 buckets):
  - Visit Intelligence: planning, timing, queues, passes
  - Attention Intelligence: highlights, artworks, sub-attractions
  - Discovery Intelligence: nearby places to explore
  - Culinary Intelligence: where to eat/drink
  - Operational Intelligence: complaints, scams, facilities, policy
"""


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_poi_advice",
            "description": "Cluster-level semantic search. Use for broad queries about what to do/see/avoid at a POI. Returns ranked cluster cards (canonical observations with source corroboration).",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi": {"type": "string", "enum": ["Louvre Museum", "Colosseum"]},
                    "query": {"type": "string"},
                    "intent_l1": {
                        "type": "string",
                        "enum": [
                            "Visit Intelligence",
                            "Attention Intelligence",
                            "Discovery Intelligence",
                            "Culinary Intelligence",
                            "Operational Intelligence",
                        ],
                        "description": "Optional L1 filter to narrow results by intent category.",
                    },
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                    "min_tier": {"type": "string", "enum": ["A", "B", "C"], "default": "B"},
                },
                "required": ["poi", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_poi_concerns",
            "description": "Top operational complaints / things to avoid for a POI. Filters to Operational Intelligence + negative sentiment, sorted by corroboration. Use for 'what should I watch out for' queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi": {"type": "string", "enum": ["Louvre Museum", "Colosseum"]},
                    "category": {
                        "type": "string",
                        "enum": [
                            "Pricing",
                            "Policy & Booking",
                            "Facilities",
                            "Visitor Complaint",
                            "Access Issue",
                            "Third-party Operator",
                        ],
                    },
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
                },
                "required": ["poi"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_poi_highlights",
            "description": "Top recommendations / things to do for a POI. Filters to Tier A + positive sentiment. Use for 'what should I see' queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi": {"type": "string", "enum": ["Louvre Museum", "Colosseum"]},
                    "intent": {
                        "type": "string",
                        "enum": [
                            "Visit Intelligence",
                            "Attention Intelligence",
                            "Discovery Intelligence",
                            "Culinary Intelligence",
                        ],
                    },
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
                },
                "required": ["poi"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_insights",
            "description": "Tier-2 granular search over per-insight rows. Use when you need exact quotes, numeric anchors, specific sub-attraction mentions, or rating/source-filtered results that cluster-level search would average out.",
            "parameters": {
                "type": "object",
                "properties": {
                    "poi": {"type": "string", "enum": ["Louvre Museum", "Colosseum"]},
                    "query": {"type": "string"},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "l1": {"type": "string"},
                            "l2": {"type": "string"},
                            "rating_min": {"type": "integer", "minimum": 1, "maximum": 5},
                            "rating_max": {"type": "integer", "minimum": 1, "maximum": 5},
                            "source": {"type": "array", "items": {"type": "string"}},
                            "insight_sentiment": {"type": "array", "items": {"type": "string"}},
                            "intent_tag": {"type": "array", "items": {"type": "string"}},
                            "has_numeric": {"type": "boolean"},
                            "sub_attraction": {"type": "string"},
                            "cluster_id": {"type": "integer"},
                            "min_freshness": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "description": "All filters AND-combined. Omit to skip a dimension.",
                    },
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
                },
                "required": ["poi", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_cluster",
            "description": "Return raw reviewer quotes for a cluster — the citation trail. Always call this after picking a top result so you can cite real evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "integer"},
                    "max_quotes": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                },
                "required": ["cluster_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_answer",
            "description": "Emit the final structured answer. Must be the LAST tool call. Every claim in `answer` must be backed by ≥2 entries in `citations`. If you can't meet that bar, set confidence=low and say so in the answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "2-4 sentence direct answer with source attribution.",
                    },
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "source_id": {"type": "string"},
                                "source_url": {"type": ["string", "null"]},
                                "quote": {"type": "string"},
                                "cluster_id": {"type": "integer"},
                                "cluster_name": {"type": "string"},
                                "rating": {"type": ["integer", "null"]},
                            },
                            "required": ["source", "source_id", "quote", "cluster_id"],
                        },
                        "minItems": 2,
                    },
                    "supporting_clusters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "cluster_id": {"type": "integer"},
                                "name": {"type": "string"},
                                "l1": {"type": "string"},
                                "size": {"type": "integer"},
                                "n_sources": {"type": "integer"},
                                "source_mix": {"type": "object"},
                            },
                        },
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "caveats": {
                        "type": "string",
                        "description": "Optional: what's missing, what's uncertain, what user should verify.",
                    },
                },
                "required": ["answer", "citations", "confidence"],
            },
        },
    },
]


# Map tool names to underlying Python callables
def _execute_tool(name: str, args: dict) -> Any:
    if name == "search_poi_advice":
        return _search(
            poi=args["poi"],
            query=args["query"],
            intent_l1=args.get("intent_l1"),
            limit=args.get("limit", 5),
            min_tier=args.get("min_tier", "B"),
        )
    if name == "list_poi_concerns":
        return _list_concerns(
            poi=args["poi"],
            category=args.get("category"),
            limit=args.get("limit", 10),
        )
    if name == "list_poi_highlights":
        return _list_highlights(
            poi=args["poi"],
            intent=args.get("intent"),
            limit=args.get("limit", 10),
        )
    if name == "search_insights":
        return _search_insights(
            poi=args["poi"],
            query=args["query"],
            filters=args.get("filters") or {},
            limit=args.get("limit", 10),
        )
    if name == "explain_cluster":
        return _explain(
            cluster_id=int(args["cluster_id"]),
            max_quotes=args.get("max_quotes", 5),
        )
    raise ValueError(f"Unknown tool: {name}")


def _summarize_result_for_llm(name: str, result: Any) -> str:
    """Compact the tool result so the LLM context stays small."""
    if name in {"search_poi_advice", "list_poi_concerns", "list_poi_highlights"}:
        rows = []
        for c in result:
            rows.append({
                "cluster_id": c["cluster_id"],
                "name": c["name"],
                "l1": c.get("l1"),
                "l2": c.get("l2"),
                "sentiment": c.get("sentiment"),
                "quality_tier": c.get("quality_tier"),
                "size": c.get("size"),
                "n_sources": c.get("n_sources"),
                "source_mix": c.get("source_mix"),
                "relevance_score": c.get("relevance_score"),
            })
        return json.dumps(rows, default=str)
    if name == "search_insights":
        rows = []
        for h in result:
            rows.append({
                "flat_idx": h.get("flat_idx"),
                "text": h.get("text"),
                "source": h.get("source"),
                "source_id": h.get("source_id"),
                "rating": h.get("rating"),
                "insight_sentiment": h.get("insight_sentiment"),
                "intent_tag": h.get("intent_tag"),
                "numeric_anchors": h.get("numeric_anchors"),
                "sub_attraction": h.get("sub_attraction"),
                "cluster_id": h.get("cluster_id"),
                "cluster_name": h.get("cluster_name"),
            })
        return json.dumps(rows, default=str)
    if name == "explain_cluster":
        return json.dumps({
            "cluster": result.get("cluster"),
            "quotes": result.get("quotes"),
        }, default=str)
    return json.dumps(result, default=str)


def ask(query: str, poi_hint: Optional[str] = None, verbose: bool = False) -> dict:
    """Run the agent loop for a single query. Returns the finalize_answer payload."""
    load_all()  # warm the store at startup
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    user_msg = query
    if poi_hint:
        user_msg = f"[POI hint: {poi_hint}]\n{query}"

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    final_payload: Optional[dict] = None
    tool_calls_log: list[dict] = []

    for it in range(MAX_TOOL_ITERATIONS):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=1500,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            if verbose:
                print(f"[iter {it}] agent emitted plain text without finalize:\n{msg.content[:300]}")
            # Force a final pass by re-asking for finalize_answer
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append({
                "role": "user",
                "content": "You did not call finalize_answer. Call it now with at least 2 citations."
            })
            continue

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if verbose:
                print(f"[iter {it}] → {name}({json.dumps(args, default=str)[:120]})")

            if name == "finalize_answer":
                final_payload = args
                tool_calls_log.append({"tool": name, "args": args, "result": "FINAL"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "ok",
                })
                break

            try:
                result = _execute_tool(name, args)
            except Exception as e:
                result = {"error": str(e)}

            summary = _summarize_result_for_llm(name, result)
            tool_calls_log.append({
                "tool": name,
                "args": args,
                "result_size": len(summary),
                "result_preview": summary[:200],
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": summary[:8000],  # cap context per call
            })

        if final_payload is not None:
            break

    if final_payload is None:
        return {
            "answer": "Agent failed to produce a final answer within iteration budget.",
            "citations": [],
            "confidence": "low",
            "caveats": "Hit MAX_TOOL_ITERATIONS without finalize_answer call.",
            "tool_calls_log": tool_calls_log,
        }

    final_payload["tool_calls_log"] = tool_calls_log
    final_payload["query"] = query
    return final_payload


# ---- CLI -------------------------------------------------------------------

DEMO_QUERIES = [
    ("Colosseum", "Should I get the Roma Pass?"),
    ("Louvre Museum", "Is the Mona Lisa worth seeing?"),
    ("Colosseum", "Are there scams I should avoid?"),
    ("Louvre Museum", "How can I avoid the queue?"),
    ("Colosseum", "What are the actual ticket prices?"),
]


def _print_human(payload: dict) -> None:
    print()
    print("Q:", payload.get("query", ""))
    print()
    print("ANSWER:", payload.get("answer", ""))
    print(f"\nCONFIDENCE: {payload.get('confidence', '?')}")
    if payload.get("caveats"):
        print(f"CAVEATS: {payload['caveats']}")
    cites = payload.get("citations") or []
    print(f"\nCITATIONS ({len(cites)}):")
    for c in cites:
        rating = c.get("rating")
        rating_s = f" ★{rating}" if rating is not None else ""
        print(f"  - [{c.get('source','?')}{rating_s}] cluster={c.get('cluster_id')}")
        print(f"      \"{(c.get('quote') or '')[:280]}\"")
    if payload.get("tool_calls_log"):
        print(f"\nTOOL CALLS: {len(payload['tool_calls_log'])}")
        for tc in payload["tool_calls_log"]:
            print(f"  - {tc['tool']}")


def main():
    p = argparse.ArgumentParser(description="ask_gems — query the Gems POI intelligence")
    p.add_argument("query", nargs="*", help="Free-text query.")
    p.add_argument("--poi", help="Optional POI hint (Louvre Museum | Colosseum).")
    p.add_argument("--json", action="store_true", help="Emit JSON only (no pretty print).")
    p.add_argument("--verbose", "-v", action="store_true", help="Stream tool-call trace.")
    p.add_argument("--demo", action="store_true", help="Run the 5-query demo set.")
    args = p.parse_args()

    if args.demo:
        for poi, q in DEMO_QUERIES:
            payload = ask(q, poi_hint=poi, verbose=args.verbose)
            if args.json:
                print(json.dumps(payload, indent=2, default=str))
                print()
            else:
                _print_human(payload)
                print("\n" + "─" * 70)
        return

    if not args.query:
        p.print_help()
        return

    q = " ".join(args.query)
    payload = ask(q, poi_hint=args.poi, verbose=args.verbose)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_human(payload)


if __name__ == "__main__":
    main()

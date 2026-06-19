"""Smoke test: can FireCrawl reliably discover + scrape Reddit threads for a POI?

Two phases against Colosseum:

  Phase 1 (search) — Run a curated set of intent templates via FireCrawl's
                     web search, scoped to reddit.com. Collect unique Reddit
                     URLs surfaced per intent.
  Phase 2 (scrape) — Scrape the top 3 surfaced URLs to markdown. Verify each
                     contains the post body + at least a handful of comments
                     with visible upvote scores.

Pass criteria (manual): Phase 1 surfaces ≥10 unique reddit.com URLs across
intents; Phase 2 markdown for each scraped URL contains post title +
multiple `[score] points` or `▲` markers indicating comment trees rendered.

Fail criteria: empty search results, scrapes returning <500 chars, no
visible comment structure in markdown.

Raw output → tests/fixtures/firecrawl/reddit_colosseum_smoketest.json
"""

import json
import os
import sys
import time

from dotenv import load_dotenv
from firecrawl.v2 import FirecrawlClient
from firecrawl.v2.types import ScrapeOptions


POI = "Colosseum"

# One representative template per intent category from the plan.
# Keep it tight — 8 queries × 10 results = ~80 search credits.
INTENT_TEMPLATES = [
    ("planning",     '"{poi}" tips'),
    ("worth_it",     'is "{poi}" worth it'),
    ("logistics",    '"{poi}" skip the line'),
    ("regret",       '"{poi}" mistake'),
    ("hidden",       'hidden gems "{poi}"'),
    ("nearby",       'things to do near "{poi}"'),
    ("food",         'where to eat near "{poi}"'),
    ("trip_report",  '"{poi}" trip report'),
]

SEARCH_LIMIT = 10
SCRAPE_TOP_N = 3
OUT = "tests/fixtures/firecrawl/reddit_colosseum_smoketest.json"


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        print("FIRECRAWL_API_KEY missing", file=sys.stderr)
        return 1

    client = FirecrawlClient(api_key=api_key)

    # =====================================================================
    # PHASE 1 — Search
    # =====================================================================
    print(f"=== PHASE 1: search ({len(INTENT_TEMPLATES)} intents × {SEARCH_LIMIT} results) ===\n")
    search_results: dict[str, dict] = {}  # intent -> raw search payload
    url_to_intents: dict[str, list[str]] = {}  # url -> [intents that surfaced it]

    for intent, template in INTENT_TEMPLATES:
        query = template.format(poi=POI)
        print(f"  [{intent:13}] {query!r}")
        t0 = time.time()
        try:
            data = client.search(
                query=query,
                limit=SEARCH_LIMIT,
                include_domains=["reddit.com"],
            )
        except Exception as e:
            print(f"    ERROR: {e}")
            search_results[intent] = {"error": str(e), "query": query}
            continue

        payload = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        elapsed = time.time() - t0
        web_results = payload.get("web") or []
        print(f"    → {len(web_results)} results in {elapsed:.1f}s")

        urls_this_intent = []
        for r in web_results:
            url = r.get("url", "")
            if "reddit.com" not in url:
                continue
            urls_this_intent.append(url)
            url_to_intents.setdefault(url, []).append(intent)

        search_results[intent] = {
            "query": query,
            "elapsed_s": elapsed,
            "result_count": len(web_results),
            "reddit_urls": urls_this_intent,
            "raw": payload,
        }

    unique_urls = list(url_to_intents.keys())
    cross_intent_urls = {u: ints for u, ints in url_to_intents.items() if len(ints) > 1}

    print(f"\n  unique reddit URLs: {len(unique_urls)}")
    print(f"  URLs surfaced by ≥2 intents (cross-intent signal): {len(cross_intent_urls)}")
    if cross_intent_urls:
        print("  sample cross-intent hits:")
        for u, ints in list(cross_intent_urls.items())[:3]:
            print(f"    {u}\n      intents: {', '.join(ints)}")

    # =====================================================================
    # PHASE 2 — Scrape top N URLs
    # =====================================================================
    print(f"\n=== PHASE 2: scrape top {SCRAPE_TOP_N} URLs ===\n")

    # Rank URLs by how many intents surfaced them (proxy for relevance).
    ranked = sorted(unique_urls, key=lambda u: -len(url_to_intents[u]))
    targets = ranked[:SCRAPE_TOP_N]
    scrapes: list[dict] = []

    for url in targets:
        print(f"  scrape: {url}")
        print(f"    surfaced by: {', '.join(url_to_intents[url])}")
        t0 = time.time()
        try:
            doc = client.scrape(url=url, formats=["markdown"], only_main_content=False)
        except Exception as e:
            print(f"    ERROR: {e}")
            scrapes.append({"url": url, "error": str(e)})
            continue

        payload = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
        elapsed = time.time() - t0
        md = payload.get("markdown") or ""
        meta = payload.get("metadata") or {}

        print(f"    → {len(md)} chars markdown in {elapsed:.1f}s | status: {meta.get('statusCode')}")
        print(f"    title: {meta.get('title', '')!r}")

        # Signal check — what we want to see in a Reddit thread scrape
        signals = {
            "comments_present":  any(kw in md.lower() for kw in ["points", "•", "ago", "reply", "share"]),
            "score_markers":     md.count("points") + md.count("▲"),
            "comment_count_hint": sum(md.lower().count(w) for w in [" ago", "reply"]),
            "looks_like_thread": "/comments/" in url or "/r/" in url,
        }
        print(f"    signals: {signals}")

        scrapes.append({
            "url": url,
            "elapsed_s": elapsed,
            "markdown_len": len(md),
            "status": meta.get("statusCode"),
            "title": meta.get("title"),
            "intents_surfacing": url_to_intents[url],
            "signals": signals,
            "raw": payload,
        })

    # =====================================================================
    # Summary table
    # =====================================================================
    print("\n=== SUMMARY ===\n")
    print(f"  {'INTENT':<13} {'RESULTS':>8} {'TIME(s)':>8}")
    for intent, _ in INTENT_TEMPLATES:
        r = search_results.get(intent, {})
        if "error" in r:
            print(f"  {intent:<13} {'ERR':>8} {r.get('elapsed_s', 0):>8.1f}")
        else:
            print(f"  {intent:<13} {r.get('result_count', 0):>8} {r.get('elapsed_s', 0):>8.1f}")
    print(f"\n  Total unique reddit URLs: {len(unique_urls)}")
    print(f"  Cross-intent URLs:        {len(cross_intent_urls)}")
    print(f"  URLs scraped successfully: {sum(1 for s in scrapes if 'error' not in s)}")
    print(f"  Avg markdown size:        "
          f"{sum(s.get('markdown_len', 0) for s in scrapes) // max(1, len(scrapes))} chars")

    # Persist
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({
            "poi": POI,
            "phase1_search": search_results,
            "phase1_unique_urls": unique_urls,
            "phase1_cross_intent": cross_intent_urls,
            "phase2_scrapes": scrapes,
        }, f, indent=2, default=str)
    print(f"\n  raw → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test: can FireCrawl get past TripAdvisor's PerimeterX bot defense?

One stealth-proxy scrape against Trevi Fountain's reviews page. Saves the raw
response to tests/fixtures/firecrawl/tripadvisor_trevi_smoketest.json.

Pass criteria (manual): markdown contains reviewer text + star ratings.
Fail criteria: 503, Cloudflare/PerimeterX challenge text, or empty body.
"""

import json
import os
import sys

from dotenv import load_dotenv
from firecrawl.v2 import FirecrawlClient


URL = "https://www.tripadvisor.com/Attraction_Review-g187791-d190131-Reviews-Trevi_Fountain-Rome_Lazio.html"
OUT = "tests/fixtures/firecrawl/tripadvisor_trevi_smoketest.json"


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        print("FIRECRAWL_API_KEY missing", file=sys.stderr)
        return 1

    client = FirecrawlClient(api_key=api_key)
    print(f"scrape: {URL}")
    print(f"  proxy=stealth, wait_for=3000ms")
    doc = client.scrape(
        url=URL,
        formats=["markdown", "html"],
        proxy="stealth",
        wait_for=3000,
        only_main_content=False,
    )

    payload = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    md = payload.get("markdown") or ""
    html = payload.get("html") or ""
    meta = payload.get("metadata") or {}
    print(f"\nsaved {len(md)} chars markdown, {len(html)} chars html → {OUT}")
    print(f"status: {meta.get('statusCode')}, title: {meta.get('title')!r}")

    print("\n=== first 2000 chars of markdown ===")
    print(md[:2000])
    print("\n=== signal check ===")
    signal_keywords = [
        "review",
        "rating",
        "stars",
        "ago",
        "Helpful",
        "Travelled",
        "Date of experience",
    ]
    for kw in signal_keywords:
        if kw.lower() in md.lower():
            print(f"  ✓ found '{kw}'")
        else:
            print(f"  ✗ missing '{kw}'")

    bot_blockers = ["captcha", "perimeterx", "px-captcha", "just a moment", "cloudflare", "access denied"]
    print("\n=== bot block check ===")
    for kw in bot_blockers:
        if kw.lower() in md.lower() or kw.lower() in html.lower():
            print(f"  !!! detected '{kw}'")
        else:
            print(f"  - clean of '{kw}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

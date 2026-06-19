"""Smoke test: harshmaur/reddit-scraper on Colosseum, two modes head-to-head.

  Mode A — URL enrichment. Feed it the cross-intent URLs already surfaced by
           Firecrawl's discovery phase. Apify's job: fetch full post + comment
           tree. Use case: hybrid path (Firecrawl finds the URLs, Apify gets
           the content).

  Mode B — Keyword search. Feed it the same intent templates Firecrawl used.
           Apify does both discovery (its built-in search) and enrichment in
           one call. Use case: Apify-only path.

Decides: do we need Firecrawl at all, or does Apify search recall the same
threads Firecrawl + Google did?

Both modes write raw items to tests/fixtures/apify/. Summary compares unique
posts, comment volume, engagement-field completeness, overlap with Firecrawl
discovery, and cost (results × $2/1000).
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from apify_client import ApifyClient


POI = "Colosseum"
ACTOR_ID = "9sHOY9RzPYGjmTHo8"  # harshmaur/reddit-scraper

# Mode A — feed cross-intent URLs from the Firecrawl smoke fixture.
FIRECRAWL_FIXTURE = "tests/fixtures/firecrawl/reddit_colosseum_smoketest.json"

# Mode B — same intent templates as the Firecrawl phase, so we can do
# apples-to-apples comparison on what each tool surfaces.
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

# Smoke caps — keep cost low (~$2 for both modes combined at $2/1k results).
MODE_A_URL_LIMIT = 3                # only top-3 cross-intent URLs
MODE_A_COMMENTS_PER_POST = 80
MODE_B_POSTS_PER_TERM = 5           # 8 terms × 5 = ~40 posts
MODE_B_COMMENTS_PER_POST = 40

OUT_DIR = Path("tests/fixtures/apify")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_firecrawl_cross_intent_urls(top_n: int) -> tuple[list[str], dict[str, list[str]]]:
    with open(FIRECRAWL_FIXTURE) as f:
        data = json.load(f)
    cross = data.get("phase1_cross_intent") or {}
    ranked = sorted(cross.keys(), key=lambda u: -len(cross[u]))
    return ranked[:top_n], cross


def run_actor(client: ApifyClient, run_input: dict, label: str) -> list[dict]:
    print(f"  starting actor: {label}")
    t0 = time.time()
    run = client.actor(ACTOR_ID).call(run_input=run_input, run_timeout=timedelta(minutes=10))
    elapsed = time.time() - t0
    if run is None:
        print("  ERROR: actor call returned None")
        return []
    # SDK 3.0 returns Pydantic Run model — use attribute access
    status = getattr(run, "status", None)
    dataset_id = getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)
    print(f"  run finished in {elapsed:.1f}s | status: {status}")
    if not dataset_id:
        print(f"  ERROR: no default_dataset_id on run object. Available attrs: {dir(run)[:20]}")
        return []
    items = list(client.dataset(dataset_id).iterate_items())
    print(f"  dataset items: {len(items)}")
    return items


def summarise_items(items: list[dict]) -> dict:
    posts = []
    comments = []
    other = []
    for it in items:
        t = it.get("dataType") or it.get("type") or it.get("__type__")
        # harshmaur's actor uses 'post' / 'comment' dataType tags. Be defensive about variations.
        if t in ("post", "submission") or it.get("postType"):
            posts.append(it)
        elif t == "comment" or it.get("commentId") or it.get("parentId"):
            comments.append(it)
        else:
            other.append(it)

    # Engagement-field completeness check — what % of posts/comments carry the fields we need.
    post_fields = ["title", "score", "upvoteRatio", "numComments", "createdAt", "subreddit", "permalink"]
    comment_fields = ["body", "score", "createdAt", "parentId", "depth", "author"]
    post_completeness = {f: sum(1 for p in posts if p.get(f) is not None) / max(1, len(posts)) for f in post_fields}
    comment_completeness = {f: sum(1 for c in comments if c.get(f) is not None) / max(1, len(comments)) for f in comment_fields}

    # Comment-tree depth distribution
    depths = [c.get("depth") for c in comments if c.get("depth") is not None]
    return {
        "post_count": len(posts),
        "comment_count": len(comments),
        "other_count": len(other),
        "unique_subreddits": len({p.get("subreddit") for p in posts if p.get("subreddit")}),
        "unique_post_ids": len({p.get("id") or p.get("postId") or p.get("url") for p in posts}),
        "post_field_completeness": post_completeness,
        "comment_field_completeness": comment_completeness,
        "max_comment_depth": max(depths) if depths else None,
        "median_comment_depth": sorted(depths)[len(depths) // 2] if depths else None,
        "sample_post_keys": sorted(posts[0].keys()) if posts else [],
        "sample_comment_keys": sorted(comments[0].keys()) if comments else [],
    }


def main() -> int:
    load_dotenv()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("APIFY_TOKEN missing", file=sys.stderr)
        return 1

    client = ApifyClient(token)

    # =====================================================================
    # MODE A — URL enrichment (hybrid: Firecrawl found URLs → Apify enriches)
    # =====================================================================
    print(f"\n=== MODE A: URL enrichment (top {MODE_A_URL_LIMIT} cross-intent URLs) ===\n")
    urls, all_cross = load_firecrawl_cross_intent_urls(MODE_A_URL_LIMIT)
    print(f"  feeding {len(urls)} URLs:")
    for u in urls:
        print(f"    {u}  ({', '.join(all_cross[u])})")

    mode_a_input = {
        "startUrls": [{"url": u} for u in urls],
        "crawlCommentsPerPost": True,
        "maxCommentsPerPost": MODE_A_COMMENTS_PER_POST,
        "maxPostsCount": len(urls),
        "fastMode": False,
    }
    mode_a_items = run_actor(client, mode_a_input, "Mode A")
    with open(OUT_DIR / "colosseum_mode_a_urls.json", "w") as f:
        json.dump(mode_a_items, f, indent=2, default=str)
    summary_a = summarise_items(mode_a_items)
    print(f"  → {summary_a['post_count']} posts, {summary_a['comment_count']} comments")
    print(f"  → max comment depth: {summary_a['max_comment_depth']}, unique subreddits: {summary_a['unique_subreddits']}")
    print(f"  → saved tests/fixtures/apify/colosseum_mode_a_urls.json")

    # =====================================================================
    # MODE B — Keyword search (Apify-only, no Firecrawl)
    # =====================================================================
    print(f"\n=== MODE B: keyword search ({len(INTENT_TEMPLATES)} intent templates) ===\n")
    search_terms = [tmpl.format(poi=POI) for _, tmpl in INTENT_TEMPLATES]
    intent_for_term = {tmpl.format(poi=POI): intent for intent, tmpl in INTENT_TEMPLATES}
    for t in search_terms:
        print(f"    {t!r}")

    mode_b_input = {
        "searchTerms": search_terms,
        "searchPosts": True,
        "searchComments": False,
        "crawlCommentsPerPost": True,
        "maxPostsCount": MODE_B_POSTS_PER_TERM * len(search_terms),
        "maxCommentsPerPost": MODE_B_COMMENTS_PER_POST,
        "searchSort": "relevance",
        "searchTime": "all",
        "fastMode": False,
    }
    mode_b_items = run_actor(client, mode_b_input, "Mode B")
    with open(OUT_DIR / "colosseum_mode_b_search.json", "w") as f:
        json.dump(mode_b_items, f, indent=2, default=str)
    summary_b = summarise_items(mode_b_items)
    print(f"  → {summary_b['post_count']} posts, {summary_b['comment_count']} comments")
    print(f"  → max comment depth: {summary_b['max_comment_depth']}, unique subreddits: {summary_b['unique_subreddits']}")
    print(f"  → saved tests/fixtures/apify/colosseum_mode_b_search.json")

    # =====================================================================
    # OVERLAP — how much does Apify search recall what Firecrawl found?
    # =====================================================================
    print("\n=== OVERLAP: Apify Mode B search vs Firecrawl discovery ===\n")
    # All reddit URLs Firecrawl surfaced
    with open(FIRECRAWL_FIXTURE) as f:
        fc_data = json.load(f)
    fc_urls = set(fc_data.get("phase1_unique_urls") or [])

    # All Apify Mode B post URLs (normalize to same shape)
    def normalize_reddit_url(u: str) -> str:
        if not u:
            return ""
        u = u.split("?")[0].rstrip("/")
        # Reddit URLs come with /r/sub/comments/<id>/... — keep through the post id
        return u.lower()

    apify_b_urls = set()
    for it in mode_b_items:
        u = it.get("url") or it.get("permalink") or ""
        if "/comments/" in u:
            apify_b_urls.add(normalize_reddit_url(u))
    fc_urls_norm = {normalize_reddit_url(u) for u in fc_urls if "/comments/" in u}

    overlap = fc_urls_norm & apify_b_urls
    fc_only = fc_urls_norm - apify_b_urls
    apify_only = apify_b_urls - fc_urls_norm
    print(f"  Firecrawl unique post URLs:  {len(fc_urls_norm)}")
    print(f"  Apify Mode B post URLs:      {len(apify_b_urls)}")
    print(f"  Overlap:                     {len(overlap)}")
    print(f"  Firecrawl-only:              {len(fc_only)}")
    print(f"  Apify-only:                  {len(apify_only)}")
    if apify_only:
        print("  sample Apify-only finds (threads Firecrawl missed):")
        for u in list(apify_only)[:5]:
            print(f"    {u}")
    if fc_only:
        print("  sample Firecrawl-only finds (threads Apify search missed):")
        for u in list(fc_only)[:5]:
            print(f"    {u}")

    # =====================================================================
    # SUMMARY
    # =====================================================================
    print("\n=== SUMMARY ===\n")
    total_a = summary_a["post_count"] + summary_a["comment_count"]
    total_b = summary_b["post_count"] + summary_b["comment_count"]
    print(f"  {'METRIC':<32} {'MODE A (URL)':>15} {'MODE B (SEARCH)':>18}")
    print(f"  {'posts':<32} {summary_a['post_count']:>15} {summary_b['post_count']:>18}")
    print(f"  {'comments':<32} {summary_a['comment_count']:>15} {summary_b['comment_count']:>18}")
    print(f"  {'unique subreddits':<32} {summary_a['unique_subreddits']:>15} {summary_b['unique_subreddits']:>18}")
    print(f"  {'max comment depth':<32} {summary_a['max_comment_depth'] or '-':>15} {summary_b['max_comment_depth'] or '-':>18}")
    print(f"  {'~cost (results × $2/1k)':<32} {f'${total_a * 0.002:.3f}':>15} {f'${total_b * 0.002:.3f}':>18}")
    print()
    print("  POST FIELD COMPLETENESS (% rows with the field set):")
    for f in summary_a["post_field_completeness"]:
        a = summary_a["post_field_completeness"].get(f, 0)
        b = summary_b["post_field_completeness"].get(f, 0)
        print(f"    {f:<28} {a*100:>14.0f}% {b*100:>17.0f}%")
    print()
    print("  COMMENT FIELD COMPLETENESS:")
    for f in summary_a["comment_field_completeness"]:
        a = summary_a["comment_field_completeness"].get(f, 0)
        b = summary_b["comment_field_completeness"].get(f, 0)
        print(f"    {f:<28} {a*100:>14.0f}% {b*100:>17.0f}%")
    print()
    print(f"  Mode A sample post keys ({len(summary_a['sample_post_keys'])} fields):")
    print(f"    {summary_a['sample_post_keys']}")
    print(f"  Mode A sample comment keys ({len(summary_a['sample_comment_keys'])} fields):")
    print(f"    {summary_a['sample_comment_keys']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

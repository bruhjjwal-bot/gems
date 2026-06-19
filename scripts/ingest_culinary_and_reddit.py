"""Targeted ingestion: Culinary blogs (FireCrawl) + Reddit (Apify), parallel.

Hard-scoped to Louvre Museum + Colosseum ONLY.

Outputs:
  l2/data/raw_blog_culinary.json
  l2/data/raw_reddit_targeted.json

Both files match the existing raw schema (source, source_id, source_uuid,
poi_id, poi_name, city, rating, text, meta).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path("/Users/headout/Documents/Gems/gems-scraper")
DATA_DIR = ROOT / "l2" / "data"
OUT_CULINARY = DATA_DIR / "raw_blog_culinary.json"
OUT_REDDIT = DATA_DIR / "raw_reddit_targeted.json"

POIS = {
    "colosseum": {
        "poi_id": "1a20c26c-e5fd-4ef7-8afd-9d90a7ccf97a",
        "poi_name": "Colosseum",
        "city": "Rome",
        "match_terms": ["colosseum", "colosseo"],
    },
    "louvre": {
        "poi_id": "ea32b51d-31b5-47c6-a359-fe3652eab62e",
        "poi_name": "Louvre Museum",
        "city": "Paris",
        "match_terms": ["louvre"],
    },
}

CULINARY_URLS = {
    "colosseum": [
        "https://www.tripsavvy.com/best-restaurants-near-the-colosseum-1547916",
        "https://theromanguy.com/italy-travel-blog/rome/best-restaurants-near-the-colosseum-rome/",
        "https://www.walksofitaly.com/blog/food-and-wine/best-food-near-colosseum-rome",
        "https://rome.eater.com/maps/best-restaurants-rome-italy",
        "https://romewise.com/restaurants-near-colosseum.html",
        "https://www.thetourguy.com/travel-blog/italy/rome/best-restaurants-near-colosseum/",
    ],
    "louvre": [
        "https://www.tripsavvy.com/best-restaurants-near-the-louvre-museum-4174670",
        "https://discoverwalks.com/blog/paris/top-10-best-places-to-eat-near-the-louvre/",
        "https://thetourguy.com/travel-blog/france/paris/best-cafes-near-the-louvre/",
        "https://paris.eater.com/maps/best-restaurants-louvre-paris",
        "https://www.theinfatuation.com/paris/guides/where-to-eat-near-the-louvre",
        "https://parisbymouth.com/where-to-eat-near-the-louvre/",
    ],
}

# Reddit targeted searches: (label, query, allowed_subreddits, poi_key)
REDDIT_SEARCHES = [
    # Colosseum
    ("col_scam",     "Colosseum scam tout warning",          ["rome", "europetravel", "italytravel"], "colosseum"),
    ("col_hidden",   "Colosseum hidden underground tip",     ["rome", "italytravel", "europetravel"], "colosseum"),
    ("col_access",   "Colosseum accessibility wheelchair",   ["rome", "europetravel", "italytravel"], "colosseum"),
    # Louvre
    ("lou_scam",     "Louvre scam pickpocket warning",       ["paris", "europetravel", "AskParis"], "louvre"),
    ("lou_hidden",   "Louvre hidden artwork tip",            ["paris", "AskParis", "europetravel"], "louvre"),
    ("lou_access",   "Louvre accessibility tip",             ["paris", "europetravel", "AskParis"], "louvre"),
]

REDDIT_POSTS_PER_SEARCH = 30
REDDIT_COMMENTS_PER_POST = 5
REDDIT_ACTOR_ID = "9sHOY9RzPYGjmTHo8"  # harshmaur/reddit-scraper


# =============================================================================
# Helpers
# =============================================================================

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
HEADING_RE = re.compile(r"^#+\s*", re.MULTILINE)
EXTRA_WS_RE = re.compile(r"\s+")
NAV_BLACKLIST = {
    "menu", "search", "subscribe", "newsletter", "log in", "sign up", "home",
    "about", "contact", "privacy policy", "terms of service", "cookie",
    "follow us", "share", "tweet", "facebook", "instagram", "twitter",
    "© ", "all rights reserved", "skip to content", "advertisement",
}


def clean_markdown(md: str) -> str:
    """Strip markdown links/images/headings, collapse whitespace."""
    if not md:
        return ""
    md = MD_IMG_RE.sub("", md)
    md = MD_LINK_RE.sub(r"\1", md)  # keep link text
    md = HEADING_RE.sub("", md)
    return md


def is_navigation_cruft(chunk: str) -> bool:
    lo = chunk.lower().strip()
    if len(lo) < 50:
        return True
    # If chunk is mostly very short lines, it's likely nav
    lines = [l for l in chunk.split("\n") if l.strip()]
    if lines and sum(len(l) for l in lines) / max(1, len(lines)) < 25:
        return True
    for tok in NAV_BLACKLIST:
        # If a nav token appears AND the whole chunk is short-ish, drop
        if tok in lo and len(lo) < 200:
            return True
    return False


def chunk_markdown(md: str, *, target_min: int = 300, target_max: int = 800) -> list[str]:
    """Split markdown into paragraph-aligned chunks of ~300-800 chars."""
    cleaned = clean_markdown(md)
    # Split on blank lines (paragraphs)
    paras = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]

    chunks: list[str] = []
    buf = ""
    for p in paras:
        # Normalize internal whitespace
        p = EXTRA_WS_RE.sub(" ", p).strip()
        if not p:
            continue
        if len(p) > target_max:
            # Long paragraph — flush buf, then split this para on sentences
            if buf:
                chunks.append(buf.strip())
                buf = ""
            sentences = re.split(r"(?<=[.!?])\s+", p)
            cur = ""
            for s in sentences:
                if len(cur) + len(s) + 1 <= target_max:
                    cur = (cur + " " + s).strip()
                else:
                    if cur:
                        chunks.append(cur.strip())
                    cur = s
            if cur:
                buf = cur
            continue
        # Normal-sized paragraph: accumulate
        if len(buf) + len(p) + 2 <= target_max:
            buf = (buf + "\n\n" + p).strip() if buf else p
        else:
            if buf:
                chunks.append(buf.strip())
            buf = p
        if len(buf) >= target_min and len(buf) >= target_max * 0.7:
            chunks.append(buf.strip())
            buf = ""
    if buf and len(buf) >= 80:
        chunks.append(buf.strip())

    # Filter cruft and dedupe near-identicals
    seen = set()
    out = []
    for c in chunks:
        if is_navigation_cruft(c):
            continue
        key = c[:120].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def stable_id(prefix: str, *parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def make_row(*, source: str, source_id: str, poi_key: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
    p = POIS[poi_key]
    return {
        "source": source,
        "source_id": source_id,
        "source_uuid": str(uuid.uuid4()),
        "poi_id": p["poi_id"],
        "poi_name": p["poi_name"],
        "city": p["city"],
        "rating": None,
        "text": text,
        "meta": meta,
    }


# =============================================================================
# Task B: Culinary FireCrawl
# =============================================================================

def task_b_culinary(out: dict[str, Any]) -> None:
    print("[B] starting culinary FireCrawl task")
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        print("[B] ERROR: FIRECRAWL_API_KEY missing, skipping task B")
        out["rows"] = []
        out["urls_scraped"] = []
        out["error"] = "no_api_key"
        return

    from firecrawl.v2 import FirecrawlClient
    client = FirecrawlClient(api_key=api_key)

    rows: list[dict[str, Any]] = []
    urls_scraped: list[dict[str, Any]] = []
    per_poi_counts = {k: {"rows": 0, "urls": 0} for k in POIS}

    for poi_key, urls in CULINARY_URLS.items():
        for url in urls:
            try:
                print(f"[B] scrape ({poi_key}): {url}")
                doc = client.scrape(
                    url=url,
                    formats=["markdown"],
                    only_main_content=True,
                    timeout=60000,
                    wait_for=2000,
                )
                payload = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
                md = payload.get("markdown") or ""
                title = (payload.get("metadata") or {}).get("title") or ""
                if not md:
                    print(f"[B]   no markdown returned ({len(md)} chars), skip")
                    urls_scraped.append({"url": url, "poi": poi_key, "chunks": 0, "ok": False})
                    continue
                chunks = chunk_markdown(md)
                kept = 0
                for ch in chunks:
                    sid = stable_id("fc", url, ch[:80])
                    row = make_row(
                        source="firecrawl_blog",
                        source_id=sid,
                        poi_key=poi_key,
                        text=ch,
                        meta={"url": url, "title": title},
                    )
                    rows.append(row)
                    kept += 1
                per_poi_counts[poi_key]["rows"] += kept
                per_poi_counts[poi_key]["urls"] += 1
                urls_scraped.append({"url": url, "poi": poi_key, "chunks": kept, "ok": True})
                print(f"[B]   ok: {kept} chunks (from {len(md)} chars markdown)")
            except Exception as e:
                print(f"[B]   ERROR scraping {url}: {type(e).__name__}: {e}")
                urls_scraped.append({"url": url, "poi": poi_key, "chunks": 0, "ok": False, "error": str(e)[:200]})
                continue

    out["rows"] = rows
    out["urls_scraped"] = urls_scraped
    out["per_poi"] = per_poi_counts
    print(f"[B] done. total rows: {len(rows)} | per-POI: {per_poi_counts}")


# =============================================================================
# Task C: Reddit targeted via Apify
# =============================================================================

def _text_mentions_poi(text: str, terms: list[str]) -> bool:
    if not text:
        return False
    lo = text.lower()
    return any(t in lo for t in terms)


def _classify_reddit_item(item: dict) -> str:
    t = item.get("dataType") or item.get("type") or item.get("__type__")
    if t in ("post", "submission") or item.get("postType"):
        return "post"
    if t == "comment" or item.get("commentId") or item.get("parentId"):
        return "comment"
    # heuristic
    if item.get("title") and item.get("url"):
        return "post"
    if item.get("body") and not item.get("title"):
        return "comment"
    return "other"


def task_c_reddit(out: dict[str, Any]) -> None:
    print("[C] starting Reddit targeted task")
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("[C] ERROR: APIFY_TOKEN missing, skipping task C")
        out["rows"] = []
        out["per_search"] = {}
        out["error"] = "no_apify_token"
        return

    from apify_client import ApifyClient
    client = ApifyClient(token)

    rows: list[dict[str, Any]] = []
    per_search: dict[str, dict[str, int]] = {}

    for label, query, subs, poi_key in REDDIT_SEARCHES:
        poi = POIS[poi_key]
        match_terms = poi["match_terms"]
        print(f"[C] search ({label}, poi={poi_key}): {query!r}")
        run_input = {
            "searchTerms": [query],
            "searchPosts": True,
            "searchComments": False,
            "crawlCommentsPerPost": True,
            "maxPostsCount": REDDIT_POSTS_PER_SEARCH,
            "maxCommentsPerPost": REDDIT_COMMENTS_PER_POST,
            "searchSort": "relevance",
            "searchTime": "all",
            "fastMode": False,
        }
        try:
            t0 = time.time()
            run = client.actor(REDDIT_ACTOR_ID).call(
                run_input=run_input,
                run_timeout=timedelta(minutes=6),
            )
            elapsed = time.time() - t0
            if run is None:
                print(f"[C]   actor returned None for {label}")
                per_search[label] = {"posts": 0, "comments": 0, "raw_items": 0}
                continue
            dataset_id = getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)
            status = getattr(run, "status", "?")
            if not dataset_id:
                print(f"[C]   no dataset id (status={status}) for {label}")
                per_search[label] = {"posts": 0, "comments": 0, "raw_items": 0}
                continue
            items = list(client.dataset(dataset_id).iterate_items())
            print(f"[C]   {label}: {len(items)} raw items in {elapsed:.1f}s (status={status})")
        except Exception as e:
            print(f"[C]   ERROR ({label}): {type(e).__name__}: {e}")
            per_search[label] = {"posts": 0, "comments": 0, "raw_items": 0, "error": str(e)[:200]}
            continue

        # Group comments under their parent post (by post id) so we can keep
        # at most one row per post + filter comments to posts that mention POI.
        posts_by_id: dict[str, dict] = {}
        comments: list[dict] = []
        for it in items:
            kind = _classify_reddit_item(it)
            if kind == "post":
                pid = it.get("id") or it.get("postId") or it.get("url")
                if pid:
                    posts_by_id[pid] = it
            elif kind == "comment":
                comments.append(it)

        posts_kept = 0
        comments_kept = 0

        # Filter posts by subreddit + POI mention
        for pid, p in posts_by_id.items():
            sub = (p.get("subreddit") or p.get("subredditName") or "").lower().lstrip("r/")
            title = p.get("title") or ""
            body = p.get("body") or p.get("selftext") or p.get("text") or ""
            mention_text = f"{title}\n{body}"
            if not _text_mentions_poi(mention_text, match_terms):
                continue
            text_content = body.strip() or title.strip()
            if len(text_content) < 30:
                continue
            sid = p.get("id") or p.get("postId") or stable_id("rdp", p.get("url") or title)
            url = p.get("url") or p.get("permalink") or ""
            row = make_row(
                source="reddit_targeted",
                source_id=f"rdp_{sid}",
                poi_key=poi_key,
                text=(title + "\n\n" + body).strip() if body else title.strip(),
                meta={
                    "url": url,
                    "title": title,
                    "subreddit": sub,
                    "score": p.get("score"),
                    "num_comments": p.get("numComments"),
                    "kind": "post",
                    "search_label": label,
                    "search_query": query,
                },
            )
            rows.append(row)
            posts_kept += 1

        # Filter comments — only keep those whose parent post mentions POI
        # (we collected POI-matching post IDs above)
        kept_post_ids = {p.get("id") or p.get("postId") for pid, p in posts_by_id.items()
                        if _text_mentions_poi((p.get("title") or "") + (p.get("body") or p.get("selftext") or ""), match_terms)}
        kept_post_ids.discard(None)
        for c in comments:
            parent_post_id = c.get("postId") or c.get("parentPostId") or c.get("submissionId")
            body = c.get("body") or c.get("text") or ""
            # Two acceptance paths:
            #   - parent post id is in our kept set, OR
            #   - the comment itself mentions the POI
            in_kept = parent_post_id in kept_post_ids if parent_post_id else False
            mentions = _text_mentions_poi(body, match_terms)
            if not (in_kept or mentions):
                continue
            if len(body.strip()) < 30:
                continue
            cid = c.get("id") or c.get("commentId") or stable_id("rdc", body[:80])
            sub = (c.get("subreddit") or c.get("subredditName") or "").lower().lstrip("r/")
            row = make_row(
                source="reddit_targeted",
                source_id=f"rdc_{cid}",
                poi_key=poi_key,
                text=body.strip(),
                meta={
                    "url": c.get("permalink") or c.get("url") or "",
                    "title": "",
                    "subreddit": sub,
                    "score": c.get("score"),
                    "parent_post_id": parent_post_id,
                    "kind": "comment",
                    "search_label": label,
                    "search_query": query,
                },
            )
            rows.append(row)
            comments_kept += 1

        per_search[label] = {
            "raw_items": len(items),
            "posts": posts_kept,
            "comments": comments_kept,
            "poi": poi_key,
        }
        print(f"[C]   {label}: kept {posts_kept} posts, {comments_kept} comments after POI filter")

    out["rows"] = rows
    out["per_search"] = per_search
    print(f"[C] done. total rows: {len(rows)}")


# =============================================================================
# Driver
# =============================================================================

def main() -> int:
    load_dotenv(ROOT / ".env")
    t_start = time.time()

    b_out: dict[str, Any] = {}
    c_out: dict[str, Any] = {}

    t_b = threading.Thread(target=task_b_culinary, args=(b_out,), name="task-b")
    t_c = threading.Thread(target=task_c_reddit, args=(c_out,), name="task-c")
    t_b.start()
    t_c.start()
    t_b.join()
    t_c.join()

    # Dedupe rows by source_id within each output (within-file, conservative)
    def dedupe(rows: list[dict]) -> list[dict]:
        seen = set()
        out = []
        for r in rows:
            if r["source_id"] in seen:
                continue
            seen.add(r["source_id"])
            out.append(r)
        return out

    b_rows = dedupe(b_out.get("rows", []))
    c_rows = dedupe(c_out.get("rows", []))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_CULINARY, "w") as f:
        json.dump(b_rows, f, indent=2, ensure_ascii=False)
    with open(OUT_REDDIT, "w") as f:
        json.dump(c_rows, f, indent=2, ensure_ascii=False)

    elapsed_min = (time.time() - t_start) / 60.0

    # ---- Report ----
    b_per_poi = b_out.get("per_poi", {})
    b_urls = b_out.get("urls_scraped", [])
    b_urls_ok = sum(1 for u in b_urls if u.get("ok"))
    b_chunks = len(b_rows)
    # Rough cost estimates:
    #  - FireCrawl: ~$0.003 / scrape with stealth (we used default proxy, even cheaper)
    #    Be conservative: $0.005/page.
    b_cost = round(b_urls_ok * 0.005, 3)
    #  - Reddit Apify: $2 / 1000 dataset items. Use raw_items if available.
    c_raw_items = sum(v.get("raw_items", 0) for v in c_out.get("per_search", {}).values())
    c_cost = round(c_raw_items * 0.002, 3)

    c_posts = sum(1 for r in c_rows if r["meta"].get("kind") == "post")
    c_comments = sum(1 for r in c_rows if r["meta"].get("kind") == "comment")

    print("\n=== REPORT ===")
    print(f"B (Culinary FC): {b_chunks} rows, {b_urls_ok}/{len(b_urls)} URLs scraped, ${b_cost}")
    for poi_key, c in b_per_poi.items():
        print(f"  {POIS[poi_key]['poi_name']}: {c['rows']} rows from {c['urls']} URLs")
    print(f"C (Reddit targeted): {c_posts} posts + {c_comments} comments, ${c_cost}")
    print(f"  By search:")
    print(f"  {'label':<14} {'poi':<10} {'raw':>5} {'posts':>6} {'cmts':>6}")
    for label, v in c_out.get("per_search", {}).items():
        print(f"  {label:<14} {v.get('poi','-'):<10} {v.get('raw_items',0):>5} {v.get('posts',0):>6} {v.get('comments',0):>6}")
    print(f"Files written:")
    print(f"  {OUT_CULINARY}")
    print(f"  {OUT_REDDIT}")
    print(f"Total time: {elapsed_min:.1f} min")
    print(f"Total cost: ~${b_cost + c_cost:.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

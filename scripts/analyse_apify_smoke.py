"""Re-analyse the saved Apify smoke fixtures with correct field names.

No actor calls; just reads tests/fixtures/apify/*.json. Compares Mode A vs
Mode B, computes proper Firecrawl/Apify overlap, and reports head-to-head.
"""

import json
import re
from collections import Counter
from pathlib import Path


FC_FIXTURE = "tests/fixtures/firecrawl/reddit_colosseum_smoketest.json"
MODE_A = "tests/fixtures/apify/colosseum_mode_a_urls.json"
MODE_B = "tests/fixtures/apify/colosseum_mode_b_search.json"


# Actor's real field names (from sample_*_keys we observed)
POST_FIELDS = ["title", "score", "upvoteRatio", "commentsCount", "createdAt",
               "subredditName", "postUrl", "engagementTotal", "scorePerHour",
               "commentToScoreRatio", "isHighEngagement", "wordCount", "subredditSubscribers"]
COMMENT_FIELDS = ["body", "score", "commentCreatedAt", "parentId", "depth",
                  "authorName", "subredditName", "controversiality", "scorePerHour",
                  "ageHours", "bodyLength"]


def split_items(items):
    posts, comments, other = [], [], []
    for it in items:
        t = it.get("dataType")
        if t == "post":
            posts.append(it)
        elif t == "comment":
            comments.append(it)
        else:
            other.append(it)
    return posts, comments, other


def field_completeness(rows, fields):
    if not rows:
        return {}
    return {f: sum(1 for r in rows if r.get(f) is not None) / len(rows) for f in fields}


def extract_post_id_from_url(url: str) -> str | None:
    # /r/<sub>/comments/<post_id>/<slug>/<optional_comment_id>
    m = re.search(r"/r/([^/]+)/comments/([a-z0-9]+)(?:/|$)", url or "", re.IGNORECASE)
    return f"r/{m.group(1).lower()}/{m.group(2).lower()}" if m else None


def main():
    # Load
    with open(FC_FIXTURE) as f:
        fc_data = json.load(f)
    with open(MODE_A) as f:
        a_items = json.load(f)
    with open(MODE_B) as f:
        b_items = json.load(f)

    a_posts, a_comments, a_other = split_items(a_items)
    b_posts, b_comments, b_other = split_items(b_items)

    # Field completeness with correct names
    print("=" * 78)
    print("POST FIELD COMPLETENESS (correct field names)")
    print("=" * 78)
    a_pc = field_completeness(a_posts, POST_FIELDS)
    b_pc = field_completeness(b_posts, POST_FIELDS)
    print(f"  {'field':<28} {'Mode A (URL)':>15} {'Mode B (search)':>17}")
    for f in POST_FIELDS:
        print(f"  {f:<28} {a_pc.get(f, 0)*100:>14.0f}% {b_pc.get(f, 0)*100:>16.0f}%")

    print()
    print("=" * 78)
    print("COMMENT FIELD COMPLETENESS")
    print("=" * 78)
    a_cc = field_completeness(a_comments, COMMENT_FIELDS)
    b_cc = field_completeness(b_comments, COMMENT_FIELDS)
    print(f"  {'field':<28} {'Mode A (URL)':>15} {'Mode B (search)':>17}")
    for f in COMMENT_FIELDS:
        print(f"  {f:<28} {a_cc.get(f, 0)*100:>14.0f}% {b_cc.get(f, 0)*100:>16.0f}%")

    # Volume
    print()
    print("=" * 78)
    print("VOLUME")
    print("=" * 78)
    print(f"  {'':<32} {'Mode A':>10} {'Mode B':>10}")
    print(f"  {'posts':<32} {len(a_posts):>10} {len(b_posts):>10}")
    print(f"  {'comments':<32} {len(a_comments):>10} {len(b_comments):>10}")
    print(f"  {'unique subreddits (posts)':<32} "
          f"{len({p.get('subredditName') for p in a_posts if p.get('subredditName')}):>10} "
          f"{len({p.get('subredditName') for p in b_posts if p.get('subredditName')}):>10}")
    print(f"  {'max comment depth':<32} "
          f"{max([c.get('depth', 0) for c in a_comments] or [0]):>10} "
          f"{max([c.get('depth', 0) for c in b_comments] or [0]):>10}")
    print(f"  {'avg comments per post':<32} "
          f"{len(a_comments) / max(len(a_posts), 1):>10.1f} "
          f"{len(b_comments) / max(len(b_posts), 1):>10.1f}")

    # Subreddit distribution (Mode B — discovery breadth)
    print()
    print("=" * 78)
    print("MODE B — SUBREDDIT DISTRIBUTION (top 15)")
    print("=" * 78)
    sub_counts = Counter(p.get("subredditName") for p in b_posts if p.get("subredditName"))
    for sub, n in sub_counts.most_common(15):
        print(f"  r/{sub:<35} {n:>4} posts")

    # OVERLAP — Firecrawl vs Apify Mode B, on POST IDs (canonical)
    print()
    print("=" * 78)
    print("OVERLAP — Firecrawl discovery vs Apify Mode B search (canonical post IDs)")
    print("=" * 78)

    fc_post_ids = set()
    for u in fc_data.get("phase1_unique_urls") or []:
        pid = extract_post_id_from_url(u)
        if pid:
            fc_post_ids.add(pid)

    # Apify Mode B post URLs come from `postUrl` field on each post item.
    apify_post_ids = set()
    for p in b_posts:
        pid = extract_post_id_from_url(p.get("postUrl") or "")
        if pid:
            apify_post_ids.add(pid)

    # Apify Mode B comments — what posts do those comments live under?
    apify_comment_post_ids = set()
    for c in b_comments:
        pid = extract_post_id_from_url(c.get("url") or "")
        if pid:
            apify_comment_post_ids.add(pid)

    overlap_posts = fc_post_ids & apify_post_ids
    fc_only_posts = fc_post_ids - apify_post_ids
    apify_only_posts = apify_post_ids - fc_post_ids

    print(f"  Firecrawl unique post IDs:                {len(fc_post_ids)}")
    print(f"  Apify Mode B post-tier IDs:               {len(apify_post_ids)}")
    print(f"  Apify Mode B post IDs (incl comment-side): {len(apify_comment_post_ids)}")
    print(f"  Overlap (Firecrawl ∩ Apify posts):         {len(overlap_posts)}")
    print(f"  Firecrawl-only:                           {len(fc_only_posts)}")
    print(f"  Apify-only:                               {len(apify_only_posts)}")

    if fc_only_posts:
        print("\n  Sample threads Firecrawl found that Apify search missed:")
        for pid in list(fc_only_posts)[:10]:
            # find original URL
            for u in fc_data.get("phase1_unique_urls") or []:
                if extract_post_id_from_url(u) == pid:
                    print(f"    {u}")
                    break

    if apify_only_posts:
        print("\n  Sample threads Apify search found that Firecrawl missed:")
        shown = 0
        for p in b_posts:
            pid = extract_post_id_from_url(p.get("postUrl") or "")
            if pid in apify_only_posts:
                print(f"    {p.get('postUrl')}  [r/{p.get('subredditName')}, score={p.get('score')}, comments={p.get('commentsCount')}]")
                shown += 1
                if shown >= 10:
                    break

    # Engagement stats — sanity-check that the rich fields are useful
    print()
    print("=" * 78)
    print("ENGAGEMENT — Mode B post score distribution (proxy for thread quality)")
    print("=" * 78)
    scores = sorted([p.get("score", 0) for p in b_posts], reverse=True)
    print(f"  top 10 post scores: {scores[:10]}")
    print(f"  median post score:  {scores[len(scores)//2] if scores else 'n/a'}")
    print(f"  posts with score≥50: {sum(1 for s in scores if s >= 50)}")
    print(f"  posts with score≥10: {sum(1 for s in scores if s >= 10)}")

    comment_scores = sorted([c.get("score", 0) for c in b_comments], reverse=True)
    print(f"  top 10 comment scores: {comment_scores[:10]}")
    print(f"  median comment score:  {comment_scores[len(comment_scores)//2] if comment_scores else 'n/a'}")

    # Sample one really rich item per kind
    print()
    print("=" * 78)
    print("SAMPLE — one Mode B post + one of its comments")
    print("=" * 78)
    if b_posts:
        p = max(b_posts, key=lambda x: x.get("score", 0) or 0)
        print(f"  Top post: r/{p.get('subredditName')} — {p.get('title')!r}")
        print(f"    score={p.get('score')}, upvoteRatio={p.get('upvoteRatio')}, "
              f"commentsCount={p.get('commentsCount')}, engagementTotal={p.get('engagementTotal')}, "
              f"isHighEngagement={p.get('isHighEngagement')}")
        print(f"    postUrl: {p.get('postUrl')}")
        body_preview = (p.get("body") or "")[:200].replace("\n", " ")
        print(f"    body: {body_preview!r}")

    if b_comments:
        c = max(b_comments, key=lambda x: x.get("score", 0) or 0)
        body_preview = (c.get("body") or "")[:200].replace("\n", " ")
        print(f"\n  Top comment: r/{c.get('subredditName')}, score={c.get('score')}, depth={c.get('depth')}")
        print(f"    body: {body_preview!r}")
        print(f"    url:  {c.get('url')}")


if __name__ == "__main__":
    main()

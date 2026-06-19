"""Reddit scraper — hybrid pipeline.

Discovery: Firecrawl /search scoped to reddit.com (Google-backed ranking,
much better recall than Reddit's native search).
Enrichment: Apify harshmaur/reddit-scraper, URL-list mode → full structured
threads with 60+ fields per post and pre-computed engagement metrics.

The intent-template library is the heart of discovery — curated queries
across 13 categories modelling how humans actually talk about visiting a
place on Reddit. See `reddit-scraper-plan.md` for the rationale per category.
"""

import os
import re
from datetime import timedelta
from decimal import Decimal
from typing import Optional, TypedDict

from db.client import get_client


def _post_id_from_url(url: str) -> Optional[str]:
    """Extract the bare post id from a `…/r/<sub>/comments/<id>/…` URL."""
    m = re.search(r"/comments/([a-z0-9]+)", url or "", re.IGNORECASE)
    return m.group(1).lower() if m else None


_FC_CLIENT = None


def _firecrawl_client():
    """Lazy singleton — defer the import so unit tests don't pay the cost."""
    global _FC_CLIENT
    if _FC_CLIENT is None:
        from firecrawl.v2 import FirecrawlClient
        key = os.environ.get("FIRECRAWL_API_KEY")
        if not key:
            raise RuntimeError("FIRECRAWL_API_KEY not set")
        _FC_CLIENT = FirecrawlClient(api_key=key)
    return _FC_CLIENT


def apify_enrich_urls(
    urls: list[str],
    max_comments_per_post: int = 60,
    max_total_charge_usd: Optional[float] = None,
) -> list[dict]:
    """Run harshmaur/reddit-scraper in URL-list mode against `urls` and return
    the raw dataset items (posts + comments interleaved by `dataType`).

    Each URL becomes a `startUrls` entry; the actor fetches the post + full
    comment tree (capped by `max_comments_per_post`). The return value is the
    direct input for `parse_apify_items`.

    Block until the run completes. 10-minute hard timeout (Apify's default is
    3 hours; we cap shorter so a runaway doesn't quietly burn credits).
    """
    if not urls:
        return []

    token = os.environ.get("APIFY_TOKEN")
    actor_id = os.environ.get("APIFY_REDDIT_ACTOR_ID")
    if not (token and actor_id):
        raise RuntimeError("APIFY_TOKEN and APIFY_REDDIT_ACTOR_ID required")

    from apify_client import ApifyClient
    client = ApifyClient(token)
    call_kwargs = {
        "run_input": {
            "startUrls": [{"url": u} for u in urls],
            "crawlCommentsPerPost": True,
            "maxCommentsPerPost": max_comments_per_post,
            "maxPostsCount": len(urls),
            "fastMode": False,
        },
        "run_timeout": timedelta(minutes=20),
    }
    if max_total_charge_usd is not None:
        call_kwargs["max_total_charge_usd"] = Decimal(str(max_total_charge_usd))
    run = client.actor(actor_id).call(**call_kwargs)
    if run is None:
        raise RuntimeError("Apify actor call returned None")
    dataset_id = getattr(run, "default_dataset_id", None)
    if not dataset_id:
        status = getattr(run, "status", "?")
        raise RuntimeError(f"Apify run has no default_dataset_id (status={status})")
    return list(client.dataset(dataset_id).iterate_items())


def firecrawl_search_reddit(query: str, limit: int = 10) -> list[dict]:
    """Search Reddit via Firecrawl (Google-backed). Returns
    `[{url, title, snippet}]` — one per matching submission thread.

    Scopes the search to `reddit.com` and drops anything that isn't a
    `/comments/<id>/` permalink (subreddit landing pages, user profiles, etc.
    are useless for our enrichment phase).
    """
    client = _firecrawl_client()
    data = client.search(
        query=query,
        limit=limit,
        include_domains=["reddit.com"],
    )
    payload = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    out: list[dict] = []
    for r in payload.get("web") or []:
        url = r.get("url") or ""
        if "reddit.com" not in url or "/comments/" not in url:
            continue
        out.append({
            "url": url,
            "title": r.get("title"),
            "snippet": r.get("description"),
        })
    return out


# Filter floor for comments. Short comments ("thanks!", "lol", "this") rarely
# carry signal. Anything ≥20 chars gets through and lets the ETL judge.
_MIN_COMMENT_BODY_LEN = 20


class Post(TypedDict, total=False):
    reddit_id: str
    title: Optional[str]
    body: Optional[str]
    community_name: Optional[str]
    post_url: Optional[str]
    score: Optional[int]
    upvote_ratio: Optional[float]
    comments_count: Optional[int]
    engagement_total: Optional[int]
    score_per_hour: Optional[float]
    is_high_engagement: Optional[bool]
    word_count: Optional[int]
    subreddit_subscribers: Optional[int]
    created_at_reddit: Optional[str]
    raw_json: dict


class Comment(TypedDict, total=False):
    reddit_id: str
    body: str
    score: Optional[int]
    score_per_hour: Optional[float]
    depth: Optional[int]
    parent_id: Optional[str]
    parent_kind: Optional[str]
    author_name: Optional[str]
    subreddit_name: Optional[str]
    created_at_reddit: Optional[str]
    controversiality: Optional[int]
    age_hours: Optional[float]
    is_submitter: Optional[bool]
    post_score: Optional[int]
    comment_url: Optional[str]
    raw_json: dict
    # Transient hint for the FK-resolution layer — NOT a DB column. Stripped
    # at save time. Carries the bare reddit_id of the parent post (the post
    # this comment lives under) so save_reddit_comments can look up the
    # right UUID FK.
    parent_post_reddit_id: str

# (intent_category, template_string, use_aliases)
# `use_aliases=True` expands the template against each POI alias in addition
# to the canonical name. Only enabled where humans naturally drop the formal
# name (planning, worth_it, trip_report) — for logistics/food/etc the formal
# name keeps relevance ranking sharper.
INTENT_TEMPLATES: list[tuple[str, str, bool]] = [
    # A. planning — biggest source of question-shaped threads with answers
    ("planning",       "{poi} tips",                       True),
    ("planning",       "visiting {poi} tomorrow",          True),
    ("planning",       "first time at {poi}",              True),
    ("planning",       "what to know before {poi}",        True),

    # B. worth_it — opinionated, regret/delight axis
    ("worth_it",       "is {poi} worth it",                True),
    ("worth_it",       "should I skip {poi}",              False),
    ("worth_it",       "is {poi} overrated",               True),

    # C. logistics — queue/entrance/ticket tactical signal
    ("logistics",      "{poi} skip the line",              False),
    ("logistics",      "best time {poi}",                  False),
    ("logistics",      "{poi} tickets",                    False),

    # D. sub_experience — multi-tier POIs (tours, undergrounds, night access)
    ("sub_experience", "{poi} guided tour worth it",       False),
    ("sub_experience", "{poi} audio guide",                False),

    # E. regret — mistakes, scams, warnings
    ("regret",         "{poi} mistake",                    False),
    ("regret",         "wish I knew {poi}",                False),
    ("regret",         "{poi} tourist trap",               False),

    # F. hidden — feeds the "things most visitors miss" Gems section
    ("hidden",         "hidden gems {poi}",                False),
    ("hidden",         "underrated {poi}",                 False),
    ("hidden",         "lesser known {poi}",               False),

    # G. nearby — visit-maximisation, gold for one-hour-after questions
    ("nearby",         "things to do near {poi}",          False),
    ("nearby",         "after visiting {poi}",             False),
    ("nearby",         "walking distance {poi}",           False),

    # H. food — feeds the "places to eat nearby" Gems section
    ("food",           "where to eat near {poi}",          False),
    ("food",           "restaurants near {poi}",           False),
    ("food",           "coffee near {poi}",                False),

    # I. photo — sensory experience, view spots
    ("photo",          "best photo {poi}",                 False),
    ("photo",          "sunset {poi}",                     False),

    # J. demographic — kids / accessibility / solo
    ("demographic",    "{poi} with kids",                  False),
    ("demographic",    "wheelchair {poi}",                 False),

    # L. trip_report — retrospective threads with dense advice
    ("trip_report",    "{poi} trip report",                True),
    ("trip_report",    "just got back from {poi}",         True),
    ("trip_report",    "my experience at {poi}",           True),

    # K. local_ask + M. comparison need extra context (city demonym,
    # comparison target) so they're handled outside this template list.
]


def parse_apify_items(items: list[dict]) -> tuple[list[Post], list[Comment]]:
    """Map raw Apify dataset items to our schema.

    Apify returns posts and comments interleaved, distinguished by `dataType`.
    Post IDs come prefixed (`t3_<id>`) but `parsedId` is the bare form — we
    use that for `reddit_id`. Comments carry `parsedPostId` matching the
    parent post's `parsedId`, which we use to denormalise `post_score` onto
    each comment so ETL can compute relative engagement without a join.

    Light filtering: drop `[deleted]` / `[removed]` bodies and comments under
    `_MIN_COMMENT_BODY_LEN` chars. Everything else is preserved raw for later
    reprocessing — see `raw_json` on every row.
    """
    posts: list[Post] = []
    raw_comments: list[dict] = []
    for it in items:
        t = it.get("dataType")
        if t == "post":
            posts.append({
                "reddit_id": it.get("parsedId") or it["id"].removeprefix("t3_"),
                "title": it.get("title"),
                "body": it.get("body"),
                "community_name": it.get("communityName"),
                "post_url": it.get("postUrl"),
                "score": it.get("score"),
                "upvote_ratio": it.get("upvoteRatio"),
                "comments_count": it.get("commentsCount"),
                "engagement_total": it.get("engagementTotal"),
                "score_per_hour": it.get("scorePerHour"),
                "is_high_engagement": it.get("isHighEngagement"),
                "word_count": it.get("wordCount"),
                "subreddit_subscribers": it.get("subredditSubscribers"),
                "created_at_reddit": it.get("createdAt"),
                "raw_json": it,
            })
        elif t == "comment":
            raw_comments.append(it)

    # Build (bare post id) → score lookup for denormalising onto comments.
    post_score_by_id: dict[str, Optional[int]] = {
        p["reddit_id"]: p["score"] for p in posts
    }

    comments: list[Comment] = []
    for c in raw_comments:
        body = (c.get("body") or "").strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue
        if len(body) < _MIN_COMMENT_BODY_LEN:
            continue
        parent_post_id = c.get("parsedPostId") or (c.get("postId") or "").removeprefix("t3_")
        comments.append({
            "reddit_id": c["id"],
            "body": body,
            "score": c.get("score"),
            "score_per_hour": c.get("scorePerHour"),
            "depth": c.get("depth"),
            "parent_id": c.get("parentId"),
            "parent_kind": c.get("parentKind"),
            "author_name": c.get("authorName"),
            "subreddit_name": c.get("subredditName"),
            "created_at_reddit": c.get("commentCreatedAt"),
            "controversiality": c.get("controversiality"),
            "age_hours": c.get("ageHours"),
            "is_submitter": c.get("isSubmitter"),
            "post_score": post_score_by_id.get(parent_post_id),
            "comment_url": c.get("url"),
            "raw_json": c,
            "parent_post_reddit_id": parent_post_id,
        })

    return posts, comments


def save_reddit_posts(poi_id: str, posts: list[Post]) -> dict[str, str]:
    """Upsert posts to `reddit_posts`, idempotent on `reddit_id`.

    Returns a `{reddit_id: id_uuid}` mapping so the orchestrator can resolve
    FK references when writing comments and link rows in subsequent slices.
    """
    if not posts:
        return {}
    db = get_client()
    rows = [{**p, "poi_id": poi_id} for p in posts]
    result = db.table("reddit_posts").upsert(rows, on_conflict="reddit_id").execute()
    return {r["reddit_id"]: r["id"] for r in (result.data or [])}


def save_poi_reddit_links(poi_id: str, specs: list[dict]) -> int:
    """Write provenance link rows to `poi_reddit_links`.

    `specs` is a list of fully-formed link dicts — each has `item_id` (uuid),
    `item_type` ('post' | 'comment'), `match_source`, `intent_category`,
    `query_term`, `discovered_via`, and optional `query_sort`. The orchestrator
    is responsible for building the inheritance — i.e. for each post surfaced
    by (intent, query, source), write one post-link row plus one
    `comment_inherited` row per child comment carrying the same intent + query.

    Idempotent via the UNIQUE(poi_id, item_id, item_type, intent_category,
    query_term, discovered_via) constraint on the table.
    """
    if not specs:
        return 0
    rows = [{**s, "poi_id": poi_id} for s in specs]
    db = get_client()
    result = db.table("poi_reddit_links").upsert(
        rows,
        on_conflict="poi_id,item_id,item_type,intent_category,query_term,discovered_via",
    ).execute()
    return len(result.data or [])


def save_reddit_comments(
    comments: list[Comment],
    post_id_map: dict[str, str],
) -> int:
    """Upsert comments to `reddit_comments`, idempotent on `reddit_id`.

    Resolves the FK `post_id` from each comment's transient
    `parent_post_reddit_id` via `post_id_map` (produced by `save_reddit_posts`).
    Orphan comments (parent not in the map) are silently skipped — they can't
    be persisted because `post_id` is NOT NULL.

    Returns the number of rows written.
    """
    if not comments:
        return 0
    rows: list[dict] = []
    for c in comments:
        parent_rid = c.get("parent_post_reddit_id")
        post_uuid = post_id_map.get(parent_rid) if parent_rid else None
        if not post_uuid:
            continue
        row = {**c, "post_id": post_uuid}
        row.pop("parent_post_reddit_id", None)  # internal hint, not a DB column
        rows.append(row)
    if not rows:
        return 0
    db = get_client()
    result = db.table("reddit_comments").upsert(rows, on_conflict="reddit_id").execute()
    return len(result.data or [])


def dedupe_by_reddit_id(rows: list[dict]) -> list[dict]:
    """Collapse duplicates by `reddit_id`, keep first occurrence. Rows with a
    missing or empty `reddit_id` are dropped silently — they can't be linked
    or upserted anyway.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        rid = r.get("reddit_id")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def scrape_poi_reddit(
    poi: dict,
    *,
    max_templates: int = 60,
    max_urls_per_template: int = 5,
    max_comments_per_post: int = 60,
    max_total_charge_usd: Optional[float] = None,
    skip_existing_urls: bool = False,
) -> dict:
    """End-to-end Reddit scrape for one POI.

    Composes every slice: build intent queries → Firecrawl discovery →
    Apify enrichment → parse → dedupe → save posts → save comments → save
    provenance link rows.

    Returns a summary `{posts_saved, comments_saved, links_saved,
    urls_discovered, intents_used}`.

    Caps are tunable to control Apify cost. Defaults aim for thorough coverage;
    the Louvre TDD test passes smaller values to keep a single live run under
    ~$1 at $2/1000 items.
    """
    poi_id = poi["id"]
    name = poi["name"]
    aliases = poi.get("aliases") or []

    # 1. Build intent queries
    queries = build_intent_queries(name, aliases)[:max_templates]
    print(f"[reddit] {name}: running {len(queries)} intent queries")

    # 2. Firecrawl discovery — record every (intent, query) that surfaced each URL
    url_to_provenance: dict[str, list[dict]] = {}
    for intent, query in queries:
        try:
            results = firecrawl_search_reddit(query, limit=max_urls_per_template * 2)
        except Exception as e:
            print(f"  [firecrawl] error on {query!r}: {e}")
            continue
        for r in results[:max_urls_per_template]:
            url = r["url"]
            url_to_provenance.setdefault(url, []).append({
                "intent_category": intent,
                "query_term": query,
                "discovered_via": "firecrawl",
                "query_sort": "firecrawl_google",
            })

    urls = list(url_to_provenance.keys())
    print(f"[reddit] {name}: {len(urls)} unique URLs across queries")

    # Skip URLs whose posts are already in DB (avoids re-paying Apify for
    # threads we already enriched). Provenance link rows are still written
    # for all surfaced URLs — even already-saved ones — so new intents
    # tagging an existing thread are captured.
    existing_reddit_ids: set[str] = set()
    if skip_existing_urls:
        db = get_client()
        existing_rows = (
            db.table("reddit_posts").select("reddit_id").eq("poi_id", poi_id).execute().data or []
        )
        existing_reddit_ids = {r["reddit_id"] for r in existing_rows}
        urls_to_enrich = [u for u in urls if (_post_id_from_url(u) or "") not in existing_reddit_ids]
        print(f"[reddit] {name}: {len(existing_reddit_ids)} already in DB, "
              f"{len(urls_to_enrich)} new to enrich")
    else:
        urls_to_enrich = urls

    if not urls:
        return {
            "posts_saved": 0, "comments_saved": 0, "links_saved": 0,
            "urls_discovered": 0, "intents_used": 0,
        }

    # 3. Apify enrichment (only on new URLs if skip_existing_urls is on)
    raw_items = apify_enrich_urls(
        urls_to_enrich,
        max_comments_per_post=max_comments_per_post,
        max_total_charge_usd=max_total_charge_usd,
    ) if urls_to_enrich else []
    print(f"[reddit] {name}: Apify returned {len(raw_items)} items")

    # 4. Parse
    posts, comments = parse_apify_items(raw_items)

    # 5. Dedupe (Apify should be unique already — defensive)
    posts = dedupe_by_reddit_id(posts)
    comments = dedupe_by_reddit_id(comments)
    print(f"[reddit] {name}: parsed {len(posts)} posts, {len(comments)} comments")

    # 6. Save posts → reddit_id → uuid map. Merge with any pre-existing
    # posts for this POI so the link-spec phase can resolve URLs that were
    # already in the DB before this run.
    post_id_map = save_reddit_posts(poi_id, posts)
    if skip_existing_urls and existing_reddit_ids:
        db = get_client()
        prior_rows = (
            db.table("reddit_posts").select("id, reddit_id").eq("poi_id", poi_id)
            .in_("reddit_id", list(existing_reddit_ids)).execute().data or []
        )
        for r in prior_rows:
            post_id_map.setdefault(r["reddit_id"], r["id"])

    # 7. Save comments (FK via post_id_map)
    comments_saved = save_reddit_comments(comments, post_id_map)

    # 8. Build provenance link specs and save.
    # Look up the post uuid for each Firecrawl URL via its embedded reddit_id;
    # for each (intent, query) that surfaced the URL, emit one post-link row
    # plus one inherited row per comment under that post.
    db = get_client()
    if post_id_map:
        comment_rows = db.table("reddit_comments").select("id, post_id").in_(
            "post_id", list(post_id_map.values())
        ).execute().data
    else:
        comment_rows = []
    comments_by_post_uuid: dict[str, list[str]] = {}
    for c in comment_rows:
        comments_by_post_uuid.setdefault(c["post_id"], []).append(c["id"])

    link_specs: list[dict] = []
    for url, provenances in url_to_provenance.items():
        rid = _post_id_from_url(url)
        post_uuid = post_id_map.get(rid) if rid else None
        if not post_uuid:
            continue
        child_comment_uuids = comments_by_post_uuid.get(post_uuid, [])
        for prov in provenances:
            link_specs.append({
                "item_id": post_uuid,
                "item_type": "post",
                "match_source": "post_search",
                **prov,
            })
            for cu in child_comment_uuids:
                link_specs.append({
                    "item_id": cu,
                    "item_type": "comment",
                    "match_source": "comment_inherited",
                    **prov,
                })

    links_saved = save_poi_reddit_links(poi_id, link_specs)
    print(f"[reddit] {name}: saved {len(posts)} posts, {comments_saved} comments, {links_saved} links")

    return {
        "posts_saved": len(posts),
        "comments_saved": comments_saved,
        "links_saved": links_saved,
        "urls_discovered": len(urls),
        "intents_used": len({intent for intent, _ in queries}),
    }


def build_intent_queries(
    name: str,
    aliases: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Expand INTENT_TEMPLATES into (intent_category, query_term) tuples.

    Aliases are applied only to templates flagged `use_aliases=True`. The same
    (intent, query) pair is returned once even if name and alias produce the
    same string — case-insensitive dedup on the query.
    """
    aliases = aliases or []
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for intent, template, use_aliases in INTENT_TEMPLATES:
        names = [name] + (aliases if use_aliases else [])
        for n in names:
            query = template.format(poi=n)
            key = (intent, query.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((intent, query))
    return out

"""TDD slices for the Reddit hybrid scraper.

Build order mirrors the plan: pure functions first (intent queries, parser,
dedupe), then DB save layers, then live Firecrawl + Apify slices. Run
`pytest tests/test_reddit.py` for the fast suite; `pytest -m live` for the
credit-spending end-to-end.
"""

import json
from collections import defaultdict
from pathlib import Path

import pytest

from db.client import get_client
from scrapers.reddit import (
    INTENT_TEMPLATES,
    apify_enrich_urls,
    build_intent_queries,
    dedupe_by_reddit_id,
    firecrawl_search_reddit,
    parse_apify_items,
    save_poi_reddit_links,
    save_reddit_comments,
    save_reddit_posts,
    scrape_poi_reddit,
)

FIXTURES = Path(__file__).parent / "fixtures" / "apify"


# Slice 1 — build_intent_queries + INTENT_TEMPLATES
# ---------------------------------------------------------------------------

# 13 categories from the plan. local_ask / comparison need extra context
# (city demonym / comparison target) that we don't pass here, so they're
# legitimately skippable. Everything else must appear.
_REQUIRED_INTENTS = {
    "planning", "worth_it", "logistics", "sub_experience", "regret",
    "hidden", "nearby", "food", "photo", "demographic", "trip_report",
}

# Categories where humans naturally drop the formal name. Aliases get expanded
# only for these — see plan rationale.
_ALIAS_INTENTS = {"planning", "worth_it", "trip_report"}


def test_build_intent_queries_covers_categories_and_uses_aliases_selectively():
    queries = build_intent_queries("Louvre Museum", ["Louvre", "Le Louvre"])

    # Enough breadth to be useful
    assert len(queries) >= 20, f"expected ≥20 expanded queries, got {len(queries)}"

    by_intent: dict[str, list[str]] = defaultdict(list)
    for intent, q in queries:
        by_intent[intent].append(q)

    # All required intents present
    missing = _REQUIRED_INTENTS - set(by_intent.keys())
    assert not missing, f"missing intent categories: {missing}"

    # Alias-bearing intents: at least one query must use a bare alias
    # (i.e. NOT contain the canonical "Louvre Museum"). That proves the
    # alias expansion fired.
    for intent in _ALIAS_INTENTS:
        qs = by_intent[intent]
        assert any("Louvre Museum" not in q and "Louvre" in q for q in qs), (
            f"{intent}: expected at least one alias-expanded query "
            f"(without 'Louvre Museum'), got: {qs!r}"
        )

    # Non-alias intents: every query must use the canonical name verbatim.
    # Catches accidental alias bleed.
    non_alias_intents = set(by_intent.keys()) - _ALIAS_INTENTS
    for intent in non_alias_intents:
        for q in by_intent[intent]:
            assert "Louvre Museum" in q, (
                f"{intent} query {q!r} should use the canonical POI name only"
            )


def test_build_intent_queries_works_without_aliases():
    # No aliases supplied → only the canonical name is used everywhere.
    queries = build_intent_queries("Trevi Fountain")
    assert len(queries) >= 20
    for _, q in queries:
        assert "Trevi Fountain" in q


# Slice 4 — parse_apify_items
# ---------------------------------------------------------------------------
# Fixture-driven: uses the existing Colosseum smoke-test dump (3 posts +
# 184 raw comments from Mode A URL enrichment). No credits burned.


def test_parse_apify_items_maps_fields_and_filters_short_or_deleted():
    fixture = json.loads((FIXTURES / "colosseum_mode_a_urls.json").read_text())
    posts, comments = parse_apify_items(fixture)

    # All 3 input posts make it through (no post-side filtering)
    assert len(posts) == 3

    # 184 raw - ~26 filtered ([deleted]/[removed] + short) ≈ 158
    assert len(comments) >= 150, f"expected ≥150 comments after filter, got {len(comments)}"

    # Post field shape — IDs must be stripped of Reddit's t3_ prefix
    for p in posts:
        assert p["reddit_id"], "reddit_id is required"
        assert not p["reddit_id"].startswith("t3_"), (
            f"reddit_id should be bare ID, got {p['reddit_id']!r}"
        )
        assert p["title"]
        assert p["score"] is not None
        assert p["upvote_ratio"] is not None
        assert p["comments_count"] is not None
        assert p["community_name"]
        assert p["post_url"]
        assert p["raw_json"]
        # Pre-computed engagement fields should round-trip
        assert p["engagement_total"] is not None
        assert p["is_high_engagement"] is not None

    # Comment field shape — including denormalised post_score
    for c in comments:
        assert c["reddit_id"]
        assert c["body"] and len(c["body"]) >= 20
        assert c["body"] not in ("[deleted]", "[removed]")
        assert c["score"] is not None
        assert c["depth"] is not None
        assert c["parent_id"]
        assert c["subreddit_name"]
        # Denormalised from the parent post — fails if parsedPostId → parsedId
        # join didn't fire.
        assert c["post_score"] is not None, (
            f"comment {c['reddit_id']!r} has no post_score; parent join failed"
        )
        assert c["raw_json"]


# Slice 5 — dedupe_by_reddit_id
# ---------------------------------------------------------------------------


def test_dedupe_by_reddit_id_collapses_duplicates_first_wins():
    rows = [
        {"reddit_id": "a", "body": "first"},
        {"reddit_id": "b", "body": "x"},
        {"reddit_id": "a", "body": "second copy"},   # dup of a — drop
        {"reddit_id": "c", "body": "y"},
        {"reddit_id": "b", "body": "z"},             # dup of b — drop
    ]
    out = dedupe_by_reddit_id(rows)
    assert [r["reddit_id"] for r in out] == ["a", "b", "c"]
    # First occurrence wins
    assert out[0]["body"] == "first"
    assert out[1]["body"] == "x"


def test_dedupe_by_reddit_id_skips_missing_id():
    rows = [
        {"reddit_id": "a"},
        {"reddit_id": ""},     # empty — skip
        {"reddit_id": "b"},
        {},                    # missing key — skip
    ]
    out = dedupe_by_reddit_id(rows)
    assert [r["reddit_id"] for r in out] == ["a", "b"]


# Slice 6 — save_reddit_posts (real DB, idempotent)
# ---------------------------------------------------------------------------


def _make_post(reddit_id: str, **overrides):
    """Minimal post dict matching the Post TypedDict shape."""
    base = {
        "reddit_id": reddit_id,
        "title": f"Test post {reddit_id}",
        "body": "test body",
        "community_name": "test_sub",
        "post_url": f"https://reddit.com/r/test/comments/{reddit_id}/test/",
        "score": 100,
        "upvote_ratio": 0.95,
        "comments_count": 50,
        "engagement_total": 150,
        "score_per_hour": 5.0,
        "is_high_engagement": True,
        "word_count": 200,
        "subreddit_subscribers": 50000,
        "created_at_reddit": "2024-01-15T10:00:00Z",
        "raw_json": {"id": f"t3_{reddit_id}", "title": f"Test post {reddit_id}"},
    }
    base.update(overrides)
    return base


def test_save_reddit_posts_is_idempotent(test_poi):
    posts = [_make_post(f"reddit_save_test_{i}") for i in range(3)]

    map1 = save_reddit_posts(test_poi, posts)
    map2 = save_reddit_posts(test_poi, posts)  # same input second time

    # Mapping covers every reddit_id and points to a uuid string each time
    assert set(map1.keys()) == {p["reddit_id"] for p in posts}
    assert all(isinstance(v, str) and len(v) >= 32 for v in map1.values())

    # Same uuid both calls — upsert behaviour, no new rows
    assert map1 == map2

    db = get_client()
    rows = db.table("reddit_posts").select("reddit_id, score, is_high_engagement, raw_json").eq("poi_id", test_poi).execute().data
    assert len(rows) == 3
    assert {r["reddit_id"] for r in rows} == set(map1.keys())
    # Verify a non-trivial field round-tripped
    assert all(r["score"] == 100 for r in rows)
    assert all(r["is_high_engagement"] is True for r in rows)
    assert all(r["raw_json"]["id"].startswith("t3_") for r in rows)


# Slice 7 — save_reddit_comments (real DB, idempotent, FK resolution)
# ---------------------------------------------------------------------------


def _make_comment(reddit_id: str, parent_rid: str, **overrides):
    """Minimal comment dict including the transient parent_post_reddit_id hint."""
    base = {
        "reddit_id": reddit_id,
        "body": "Test comment body with enough characters to pass the filter floor",
        "score": 10,
        "score_per_hour": 1.0,
        "depth": 0,
        "parent_id": f"t3_{parent_rid}",
        "parent_kind": "t3",
        "author_name": "test_user",
        "subreddit_name": "test_sub",
        "created_at_reddit": "2024-01-15T10:30:00Z",
        "controversiality": 0,
        "age_hours": 100.0,
        "is_submitter": False,
        "post_score": 100,
        "comment_url": f"https://reddit.com/r/test/comments/{parent_rid}/test/{reddit_id}",
        "raw_json": {"id": reddit_id},
        "parent_post_reddit_id": parent_rid,
    }
    base.update(overrides)
    return base


def test_save_reddit_comments_is_idempotent_and_resolves_fk(test_poi):
    # Stand up a parent post first; slice-7 needs an existing post_id_map.
    parent_rid = "c_test_parent_xyz"
    post_id_map = save_reddit_posts(test_poi, [_make_post(parent_rid)])

    comments = [
        _make_comment("c_save_test_1", parent_rid=parent_rid),
        _make_comment("c_save_test_2", parent_rid=parent_rid),
        _make_comment("c_save_test_3", parent_rid=parent_rid),
    ]

    n1 = save_reddit_comments(comments, post_id_map)
    n2 = save_reddit_comments(comments, post_id_map)
    assert n1 == 3
    assert n2 == 3  # upsert returns the same rows

    db = get_client()
    parent_uuid = post_id_map[parent_rid]
    rows = db.table("reddit_comments").select("reddit_id, post_id, body").eq("post_id", parent_uuid).execute().data
    assert len(rows) == 3
    assert all(r["post_id"] == parent_uuid for r in rows)
    assert {r["reddit_id"] for r in rows} == {f"c_save_test_{i}" for i in (1, 2, 3)}


def test_save_reddit_comments_skips_orphans(test_poi):
    parent_rid = "c_orphan_parent"
    post_id_map = save_reddit_posts(test_poi, [_make_post(parent_rid)])

    comments = [
        _make_comment("c_orphan_legit", parent_rid=parent_rid),
        _make_comment("c_orphan_dropped", parent_rid="post_not_in_map"),
    ]
    n = save_reddit_comments(comments, post_id_map)
    assert n == 1, "orphan comment should be silently dropped"

    db = get_client()
    rows = db.table("reddit_comments").select("reddit_id").eq("reddit_id", "c_orphan_dropped").execute().data
    assert not rows, "orphan should not be in DB"


# Slice 8 — save_poi_reddit_links (real DB, full provenance)
# ---------------------------------------------------------------------------


def test_save_poi_reddit_links_writes_inherited_rows_for_each_intent(test_poi):
    # Stand up one post + 3 comments under it
    parent_rid = "c_links_parent"
    post_id_map = save_reddit_posts(test_poi, [_make_post(parent_rid)])
    comments = [_make_comment(f"c_link_test_{i}", parent_rid=parent_rid) for i in range(3)]
    save_reddit_comments(comments, post_id_map)

    db = get_client()
    comment_rows = (
        db.table("reddit_comments")
        .select("id, reddit_id")
        .in_("reddit_id", [f"c_link_test_{i}" for i in range(3)])
        .execute()
        .data
    )
    comment_uuid_by_rid = {r["reddit_id"]: r["id"] for r in comment_rows}
    post_uuid = post_id_map[parent_rid]

    # Two distinct (intent, query) provenance paths surfaced the same post.
    # Each path produces a post-link row + one inherited row per comment.
    specs = []
    paths = [
        ("planning", "Louvre Museum tips"),
        ("hidden",   "hidden gems Louvre"),
    ]
    for intent, query in paths:
        specs.append({
            "item_id": post_uuid,
            "item_type": "post",
            "match_source": "post_search",
            "intent_category": intent,
            "query_term": query,
            "discovered_via": "firecrawl",
            "query_sort": "firecrawl_google",
        })
        for c_uuid in comment_uuid_by_rid.values():
            specs.append({
                "item_id": c_uuid,
                "item_type": "comment",
                "match_source": "comment_inherited",
                "intent_category": intent,
                "query_term": query,
                "discovered_via": "firecrawl",
                "query_sort": "firecrawl_google",
            })

    # 2 paths × (1 post + 3 comments) = 8 link rows
    n1 = save_poi_reddit_links(test_poi, specs)
    n2 = save_poi_reddit_links(test_poi, specs)  # rerun is a no-op
    assert n1 == 8
    assert n2 == 8

    rows = (
        db.table("poi_reddit_links")
        .select("intent_category, item_type, match_source")
        .eq("poi_id", test_poi)
        .execute()
        .data
    )
    assert len(rows) == 8
    assert {r["intent_category"] for r in rows} == {"planning", "hidden"}
    assert sum(1 for r in rows if r["item_type"] == "post") == 2          # 2 intents × 1 post
    assert sum(1 for r in rows if r["item_type"] == "comment") == 6       # 2 intents × 3 comments
    assert {r["match_source"] for r in rows} == {"post_search", "comment_inherited"}


# Slice 2 — firecrawl_search_reddit (live — cheap, ~3 credits)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_firecrawl_search_reddit_returns_louvre_threads():
    results = firecrawl_search_reddit('"Louvre Museum" tips', limit=10)

    assert len(results) >= 3, f"expected ≥3 reddit threads, got {len(results)}"
    for r in results:
        assert "reddit.com" in r["url"]
        assert "/comments/" in r["url"]
        assert r["title"], f"missing title on result {r!r}"
        # snippet often present; not strictly required (Google sometimes
        # returns blank descriptions for low-content pages).


# Slice 3 — apify_enrich_urls (live — records louvre fixture)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_apify_enrich_returns_posts_and_comments(tmp_path):
    # Two known-good Louvre URLs from Firecrawl. If Slice 2 has been run
    # interactively the exact URLs may differ, but these are stable Reddit
    # threads about the Louvre that should always exist.
    urls = [
        "https://www.reddit.com/r/ParisTravelGuide/comments/16e9wgs/best_way_to_visit_the_louvre/",
        "https://www.reddit.com/r/travel/comments/1abj2qj/louvre_tips_tricks/",
    ]
    items = apify_enrich_urls(urls, max_comments_per_post=30)

    posts = [it for it in items if it.get("dataType") == "post"]
    comments = [it for it in items if it.get("dataType") == "comment"]
    assert len(posts) >= 1, f"expected ≥1 post item, got {len(posts)}"
    assert len(comments) >= 10, f"expected ≥10 comment items, got {len(comments)}"

    # Record as a fixture for future regression tests (so parser/save tests can
    # exercise real Louvre data without burning credits).
    fixture_path = FIXTURES / "louvre_mode_a_urls.json"
    fixture_path.write_text(json.dumps(items, indent=2, default=str))


# Slice 9 — scrape_poi_reddit end-to-end (live — the big one, ~$1)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_scrape_louvre_end_to_end():
    db = get_client()
    louvre_rows = (
        db.table("pois")
        .select("id, name, aliases")
        .eq("name", "Louvre Museum")
        .execute()
        .data
    )
    assert louvre_rows, "Louvre Museum POI row missing — run the seed migration"
    louvre = louvre_rows[0]
    poi_id = louvre["id"]

    # Purge any previous Reddit data for Louvre so the test is repeatable.
    # Order: links → comments (via post_ids) → posts.
    db.table("poi_reddit_links").delete().eq("poi_id", poi_id).execute()
    post_rows = db.table("reddit_posts").select("id").eq("poi_id", poi_id).execute().data or []
    for p in post_rows:
        db.table("reddit_comments").delete().eq("post_id", p["id"]).execute()
    db.table("reddit_posts").delete().eq("poi_id", poi_id).execute()

    # Modest caps — keep one full live run under ~$1 at $2/1000 items.
    result = scrape_poi_reddit(
        louvre,
        max_templates=10,
        max_urls_per_template=5,
        max_comments_per_post=40,
    )

    assert result["posts_saved"] >= 10, f"too few posts saved: {result}"
    assert result["comments_saved"] >= 100, f"too few comments saved: {result}"
    assert result["links_saved"] >= 50, f"too few link rows saved: {result}"

    # ≥3 distinct intent_categories surfaced something
    intent_rows = (
        db.table("poi_reddit_links")
        .select("intent_category")
        .eq("poi_id", poi_id)
        .execute()
        .data
    )
    assert len({r["intent_category"] for r in intent_rows}) >= 3, (
        f"expected ≥3 distinct intents to fire, got: "
        f"{ {r['intent_category'] for r in intent_rows} }"
    )

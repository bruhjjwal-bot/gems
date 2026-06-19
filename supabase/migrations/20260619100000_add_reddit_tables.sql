-- Reddit hybrid pipeline: Firecrawl /search discovers URLs scoped by intent
-- template; Apify (harshmaur/reddit-scraper) enriches each URL into a full
-- thread. We store posts and comments separately and link them to POIs through
-- a junction that carries the full provenance of how each item was surfaced.

-- One row per Reddit submission. Stable on reddit_id so reruns are idempotent.
-- raw_json holds the full Apify item for later reprocessing with better
-- extraction prompts.
CREATE TABLE IF NOT EXISTS reddit_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  reddit_id text UNIQUE NOT NULL,            -- e.g. '1548ivb'
  title text,
  body text,
  community_name text,                       -- e.g. 'rome' (no 'r/' prefix)
  post_url text,
  score int,
  upvote_ratio numeric,
  comments_count int,
  engagement_total int,                      -- Apify pre-computed
  score_per_hour numeric,                    -- Apify pre-computed
  is_high_engagement boolean,                -- Apify pre-computed
  word_count int,
  subreddit_subscribers int,
  created_at_reddit timestamptz,
  raw_json jsonb,
  scraped_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reddit_posts_poi_id_idx ON reddit_posts(poi_id);
CREATE INDEX IF NOT EXISTS reddit_posts_community_name_idx ON reddit_posts(community_name);

-- One row per comment. parent_id is the Reddit thread parent (post or comment),
-- not a SQL FK. post_score is denormalised from reddit_posts.score so the ETL
-- can compute relative engagement (comment.score / post.score) without a join.
CREATE TABLE IF NOT EXISTS reddit_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id uuid NOT NULL REFERENCES reddit_posts(id),
  reddit_id text UNIQUE NOT NULL,
  body text,
  score int,
  score_per_hour numeric,
  depth int,
  parent_id text,                            -- Reddit's parent (not SQL FK)
  parent_kind text,                          -- 't1' (comment) or 't3' (post)
  author_name text,
  subreddit_name text,
  created_at_reddit timestamptz,
  controversiality int,
  age_hours numeric,
  is_submitter boolean,
  post_score int,                            -- denormalised from reddit_posts.score
  comment_url text,
  raw_json jsonb,
  scraped_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reddit_comments_post_id_idx ON reddit_comments(post_id);
CREATE INDEX IF NOT EXISTS reddit_comments_parent_id_idx ON reddit_comments(parent_id);

-- Junction with provenance. Multiple rows per (poi, item) are intentional —
-- each row captures one discovery path that surfaced this item, and the
-- multiplicity is itself signal ("this thread surfaced under 3 different
-- intent searches → high confidence on-topic"). Idempotency is enforced by
-- the UNIQUE tuple covering the full provenance, so reruns are no-ops.
--
-- item_id is polymorphic — points to reddit_posts.id when item_type='post',
-- reddit_comments.id when item_type='comment'. No SQL FK because Postgres
-- can't enforce polymorphic references cleanly without triggers.
CREATE TABLE IF NOT EXISTS poi_reddit_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  item_id uuid NOT NULL,
  item_type text NOT NULL CHECK (item_type IN ('post', 'comment')),
  match_source text NOT NULL CHECK (match_source IN (
    'post_search', 'comment_inherited', 'comment_search'
  )),
  intent_category text NOT NULL CHECK (intent_category IN (
    'planning', 'worth_it', 'logistics', 'sub_experience', 'regret',
    'hidden', 'nearby', 'food', 'photo', 'demographic',
    'local_ask', 'trip_report', 'comparison', 'direct_mention'
  )),
  query_term text NOT NULL,
  discovered_via text NOT NULL CHECK (discovered_via IN (
    'firecrawl', 'apify', 'pullpush'
  )),
  query_sort text,                           -- 'firecrawl_google' | 'top_all' | 'new_year' | null
  created_at timestamptz DEFAULT now(),
  UNIQUE (poi_id, item_id, item_type, intent_category, query_term, discovered_via)
);

CREATE INDEX IF NOT EXISTS poi_reddit_links_poi_id_idx ON poi_reddit_links(poi_id);
CREATE INDEX IF NOT EXISTS poi_reddit_links_item_idx ON poi_reddit_links(item_id, item_type);
CREATE INDEX IF NOT EXISTS poi_reddit_links_intent_idx ON poi_reddit_links(intent_category);

-- Seed Louvre aliases for the TDD test target.
UPDATE pois SET aliases = ARRAY['Louvre', 'Le Louvre', 'Musée du Louvre']
WHERE name = 'Louvre Museum';

-- Persists SerpApi pagination state per (poi, sort_mode) so each script run resumes
-- exactly where the last one stopped — no re-fetching pages already in google_reviews.
CREATE TABLE IF NOT EXISTS google_reviews_cursors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  sort_mode text NOT NULL,
  next_page_token text,
  exhausted boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(poi_id, sort_mode)
);

CREATE INDEX IF NOT EXISTS google_reviews_cursors_poi_id_idx ON google_reviews_cursors(poi_id);

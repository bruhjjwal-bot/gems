-- Google Maps Place ID per POI (manually seeded; see context/place-ids.md).
ALTER TABLE pois ADD COLUMN IF NOT EXISTS place_id text;

-- Google reviews fetched via SerpApi google_maps_reviews engine.
CREATE TABLE IF NOT EXISTS google_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  review_id text UNIQUE NOT NULL,
  author text,
  rating int CHECK (rating BETWEEN 1 AND 5),
  text text,
  relative_time text,
  likes int DEFAULT 0,
  sort_mode text,  -- 'most_relevant' | 'newest' | 'highest' | 'lowest'
  scraped_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS google_reviews_poi_id_idx ON google_reviews(poi_id);

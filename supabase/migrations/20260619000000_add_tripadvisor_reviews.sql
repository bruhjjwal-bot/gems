-- TripAdvisor reviews scraped via FireCrawl. Mirrors google_reviews shape but
-- with TripAdvisor-specific fields: visit_date / written_date are kept as text
-- (TripAdvisor uses 'Jun 2026' / 'June 16, 2026' formats), trip_type captures
-- the 'Couples'/'Solo'/'Family'/'Business' label per review.

ALTER TABLE pois ADD COLUMN IF NOT EXISTS ta_url text;

CREATE TABLE IF NOT EXISTS tripadvisor_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  review_id text UNIQUE NOT NULL,            -- 'r1064636770' from ShowUserReviews permalink
  author text,
  author_profile text,                       -- profile slug, e.g. 'moniquerY4512LP'
  contributions int,
  reviewer_location text,
  rating int CHECK (rating BETWEEN 1 AND 5),
  title text,
  body text,
  visit_date text,                           -- 'Jun 2026'
  trip_type text,                            -- 'Couples' | 'Solo' | 'Family' | 'Business' | 'Friends'
  written_date text,                         -- 'June 16, 2026'
  helpful_count int DEFAULT 0,
  sort_mode text,                            -- 'most_recent' | 'detailed' | 'highest' | 'lowest'
  scraped_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tripadvisor_reviews_poi_id_idx ON tripadvisor_reviews(poi_id);

CREATE TABLE IF NOT EXISTS tripadvisor_review_cursors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  sort_mode text NOT NULL,
  next_offset int,                           -- pagination offset (e.g. 10 = next URL is -or10-)
  exhausted boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(poi_id, sort_mode)
);

CREATE INDEX IF NOT EXISTS tripadvisor_review_cursors_poi_id_idx ON tripadvisor_review_cursors(poi_id);

-- Seed canonical TripAdvisor URLs (manually sourced via search).
UPDATE pois SET ta_url = 'https://www.tripadvisor.com/Attraction_Review-g187147-d188151-Reviews-Eiffel_Tower-Paris_Ile_de_France.html'
  WHERE name = 'Eiffel Tower';
UPDATE pois SET ta_url = 'https://www.tripadvisor.com/Attraction_Review-g187147-d188757-Reviews-Louvre_Museum-Paris_Ile_de_France.html'
  WHERE name = 'Louvre Museum';
UPDATE pois SET ta_url = 'https://www.tripadvisor.com/Attraction_Review-g187148-d188681-Reviews-Palace_of_Versailles-Versailles_Yvelines_Ile_de_France.html'
  WHERE name = 'Palace of Versailles';
UPDATE pois SET ta_url = 'https://www.tripadvisor.com/Attraction_Review-g187791-d192285-Reviews-Colosseum-Rome_Lazio.html'
  WHERE name = 'Colosseum';
UPDATE pois SET ta_url = 'https://www.tripadvisor.com/Attraction_Review-g187791-d190131-Reviews-Trevi_Fountain-Rome_Lazio.html'
  WHERE name = 'Trevi Fountain';
UPDATE pois SET ta_url = 'https://www.tripadvisor.com/Attraction_Review-g187793-d191000-Reviews-Vatican_Museums-Vatican_City_Lazio.html'
  WHERE name = 'Vatican Museums';

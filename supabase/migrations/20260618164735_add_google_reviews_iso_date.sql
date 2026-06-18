-- SerpApi returns iso_date (ISO 8601 timestamp) per review; persist alongside relative_time.
ALTER TABLE google_reviews ADD COLUMN IF NOT EXISTS iso_date timestamptz;

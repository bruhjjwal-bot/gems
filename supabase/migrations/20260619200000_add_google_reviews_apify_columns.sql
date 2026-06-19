-- New columns populated by the Apify compass/Google-Maps-Reviews-Scraper enrichment run.
-- All nullable so existing SerpApi rows are unaffected.
ALTER TABLE google_reviews ADD COLUMN IF NOT EXISTS is_local_guide boolean;
ALTER TABLE google_reviews ADD COLUMN IF NOT EXISTS reviewer_review_count int;
ALTER TABLE google_reviews ADD COLUMN IF NOT EXISTS owner_response text;
ALTER TABLE google_reviews ADD COLUMN IF NOT EXISTS review_url text;
ALTER TABLE google_reviews ADD COLUMN IF NOT EXISTS original_language varchar(10);

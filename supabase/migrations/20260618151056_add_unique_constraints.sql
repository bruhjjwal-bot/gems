-- The youtube transcript scraper upserts on video_id; needs a unique constraint.
ALTER TABLE youtube_transcripts
  ADD CONSTRAINT youtube_transcripts_video_id_unique UNIQUE (video_id);

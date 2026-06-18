-- POIs: the core entity. One row per attraction we scrape.
CREATE TABLE pois (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  city text NOT NULL,
  country text NOT NULL,
  aliases text[] DEFAULT '{}'::text[],
  category text,
  created_at timestamptz DEFAULT now()
);

-- YouTube videos discovered via yt-dlp search per POI.
CREATE TABLE youtube_videos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  poi_id uuid NOT NULL REFERENCES pois(id),
  video_id text UNIQUE NOT NULL,
  title text,
  url text,
  format text NOT NULL CHECK (format IN ('short', 'long')),
  views bigint DEFAULT 0,
  likes bigint DEFAULT 0,
  comment_count bigint DEFAULT 0,
  duration_seconds int DEFAULT 0,
  score numeric DEFAULT 0,
  published_at timestamptz,
  scraped_at timestamptz DEFAULT now()
);

-- One transcript per video (auto-captions or uploaded captions).
CREATE TABLE youtube_transcripts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL REFERENCES youtube_videos(id),
  language text DEFAULT 'en',
  full_text text,
  segments_json jsonb,
  scraped_at timestamptz DEFAULT now()
);

-- Top comments per video (sorted by 'top', up to 100 per video).
CREATE TABLE youtube_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL REFERENCES youtube_videos(id),
  comment_id text UNIQUE NOT NULL,
  author text,
  text text,
  likes bigint DEFAULT 0,
  published_at timestamptz,
  scraped_at timestamptz DEFAULT now()
);

CREATE INDEX youtube_videos_poi_id_idx ON youtube_videos(poi_id);
CREATE INDEX youtube_comments_video_id_idx ON youtube_comments(video_id);

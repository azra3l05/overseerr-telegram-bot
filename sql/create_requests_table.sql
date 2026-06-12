-- create_requests_table.sql
-- Create table to store Telegram bot requests in Postgres

CREATE TABLE IF NOT EXISTS public.telegram_requests (
    id SERIAL PRIMARY KEY,
    user_name TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    season INTEGER,
    library_name TEXT,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tmdb_id INTEGER,  -- Optional: store for easier linking
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_telegram_requests_user ON public.telegram_requests(user_name);
CREATE INDEX IF NOT EXISTS idx_telegram_requests_timestamp ON public.telegram_requests(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_telegram_requests_type ON public.telegram_requests(media_type);
CREATE INDEX IF NOT EXISTS idx_telegram_requests_tmdb ON public.telegram_requests(tmdb_id);

-- Create a view for easy querying
CREATE OR REPLACE VIEW public.telegram_requests_summary AS
SELECT 
    user_name,
    COUNT(*) as total_requests,
    COUNT(*) FILTER (WHERE media_type = 'movie') as movie_requests,
    COUNT(*) FILTER (WHERE media_type = 'tv') as tv_requests,
    MAX(requested_at) as last_request,
    MIN(requested_at) as first_request
FROM public.telegram_requests
GROUP BY user_name;

COMMENT ON TABLE public.telegram_requests IS 'Telegram bot media requests log';
COMMENT ON COLUMN public.telegram_requests.user_name IS 'Telegram username with handle';
COMMENT ON COLUMN public.telegram_requests.title IS 'Movie or TV show title';
COMMENT ON COLUMN public.telegram_requests.media_type IS 'Type: movie or tv';
COMMENT ON COLUMN public.telegram_requests.season IS 'Season number for TV shows';
COMMENT ON COLUMN public.telegram_requests.library_name IS 'Target library (e.g., 🇺🇸 English)';
COMMENT ON COLUMN public.telegram_requests.requested_at IS 'When the request was made';
COMMENT ON COLUMN public.telegram_requests.tmdb_id IS 'TMDB ID for linking';

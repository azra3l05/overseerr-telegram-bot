-- Add status tracking columns to telegram_requests table
-- Run this migration on the database

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS overseerr_request_id INTEGER;

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS request_status VARCHAR(20) DEFAULT 'PENDING';

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS last_status_check TIMESTAMP;

-- Create index for faster status lookups
CREATE INDEX IF NOT EXISTS idx_telegram_requests_status
ON public.telegram_requests(request_status);

CREATE INDEX IF NOT EXISTS idx_telegram_requests_tmdb
ON public.telegram_requests(tmdb_id, media_type);

-- Update existing rows to set default status
UPDATE public.telegram_requests
SET request_status = 'PENDING'
WHERE request_status IS NULL;

-- Show results
SELECT
    COUNT(*) as total_requests,
    COUNT(CASE WHEN request_status = 'PENDING' THEN 1 END) as pending,
    COUNT(CASE WHEN overseerr_request_id IS NOT NULL THEN 1 END) as with_overseerr_id
FROM public.telegram_requests;

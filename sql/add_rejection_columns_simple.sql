-- Add rejection tracking columns to telegram_requests table
-- Simplified version without UPDATE to avoid table locks

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS rejected BOOLEAN DEFAULT FALSE;

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMP;

ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS rejected_by VARCHAR(100);

-- Create index for rejected requests
CREATE INDEX IF NOT EXISTS idx_telegram_requests_rejected
ON public.telegram_requests(rejected);

-- Show results
SELECT
    COUNT(*) as total_requests,
    COUNT(telegram_user_id) as with_user_id,
    COUNT(CASE WHEN rejected = TRUE THEN 1 END) as rejected_count
FROM public.telegram_requests;

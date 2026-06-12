-- Migration: Add priority column to telegram_requests table
-- Date: 2026-03-21
-- Feature: Request Prioritization

-- Add priority column with default 'normal'
ALTER TABLE public.telegram_requests
ADD COLUMN IF NOT EXISTS priority VARCHAR(10) DEFAULT 'normal';

-- Add check constraint for valid priority values
ALTER TABLE public.telegram_requests
DROP CONSTRAINT IF EXISTS check_priority_valid;

ALTER TABLE public.telegram_requests
ADD CONSTRAINT check_priority_valid
CHECK (priority IN ('high', 'normal', 'low'));

-- Add index for faster sorting by priority
CREATE INDEX IF NOT EXISTS idx_telegram_requests_priority
ON public.telegram_requests(priority, requested_at DESC);

-- Update any existing NULL priorities to 'normal'
UPDATE public.telegram_requests
SET priority = 'normal'
WHERE priority IS NULL;

-- Verify migration
SELECT COUNT(*) as total_requests,
       COUNT(CASE WHEN priority = 'high' THEN 1 END) as high_priority,
       COUNT(CASE WHEN priority = 'normal' THEN 1 END) as normal_priority,
       COUNT(CASE WHEN priority = 'low' THEN 1 END) as low_priority
FROM public.telegram_requests;

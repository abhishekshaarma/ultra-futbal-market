-- SQL script to manually create user profile
-- Run this in your Supabase SQL Editor

-- Create user profile for the current user
INSERT INTO public.users (
    id,
    username,
    display_name,
    balance,
    total_volume,
    created_at
) VALUES (
    '9d626b36-4f08-4f7b-b0ea-036ac880be3e',  -- Your user ID from the logs
    'dirtydean',                              -- Username from email
    'dirtydean',                              -- Display name
    1000.00,                                  -- Starting balance
    0.0,                                      -- Starting volume
    NOW()                                     -- Current timestamp
) ON CONFLICT (id) DO UPDATE SET
    username = EXCLUDED.username,
    display_name = EXCLUDED.display_name,
    balance = EXCLUDED.balance,
    total_volume = EXCLUDED.total_volume;

-- Verify the user was created
SELECT * FROM public.users WHERE id = '9d626b36-4f08-4f7b-b0ea-036ac880be3e';

-- Check total users count
SELECT COUNT(*) as total_users FROM public.users; 
# Manual User Profile Fix

## Problem
The user exists in Supabase auth but not in the `public.users` table due to Row Level Security (RLS) policies blocking automatic user profile creation.

## Solution Options

### Option 1: Use the Debug Page (Recommended)
1. Log into your account
2. Navigate to `/debug` (Debug button in navbar)
3. Click "Create My Profile" button
4. This will use the admin client to bypass RLS and create your profile

### Option 2: Manual SQL Insert
If the debug page doesn't work, you can manually insert the user profile using SQL in your Supabase dashboard:

```sql
INSERT INTO public.users (
    id,
    username,
    display_name,
    email,
    balance,
    total_volume,
    created_at
) VALUES (
    '93ff858f-df2a-4eb2-b9d3-539d968116fe',  -- Your user ID from the logs
    'your_username',                         -- Replace with your email username
    'your_username',                         -- Replace with your email username
    'your_email@example.com',                -- Replace with your email
    1000.00,                                 -- Starting balance
    0.0,                                     -- Starting volume
    NOW()                                    -- Current timestamp
);
```

### Option 3: Fix RLS Policies
To prevent this issue in the future, you can modify the RLS policies in Supabase:

1. Go to your Supabase dashboard
2. Navigate to Authentication > Policies
3. Find the `users` table
4. Add a policy that allows authenticated users to insert their own profile:

```sql
CREATE POLICY "Users can insert their own profile" ON public.users
FOR INSERT WITH CHECK (auth.uid() = id);
```

## Environment Variables
Make sure you have the service role key set in your `.env` file:

```
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here
```

The service role key can be found in your Supabase dashboard under Settings > API.

## Verification
After fixing, you can verify the user profile was created by:
1. Going to `/debug`
2. Clicking "Check All Users" - you should see your user in the list
3. Clicking "Check My Balance" - you should see your balance
4. Trying to place a trade - it should work without foreign key errors 
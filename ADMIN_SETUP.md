# Market Resolution Guide

## Admin Access

The market resolution functionality is now working! Since you already have an admin user set up, you can directly use the resolution features.

## Using Market Resolution

As an admin user:

1. **Go to any market detail page** (e.g., `/markets/market-id`)
2. **Look for the "Resolve Market" panel** - it should appear for admin users
3. **Preview the resolution** by clicking "Preview YES" or "Preview NO" to see what would happen
4. **Resolve the market** by clicking "Resolve as YES" or "Resolve as NO"

## What Happens When You Resolve a Market

1. **Market status changes** from "active" to "resolved"
2. **All open orders are cancelled**
3. **Users with winning positions get paid** $1 per winning share
4. **Trading stops** on that market
5. **Payouts are automatically processed** to user balances

## Admin Features Available

- ✅ **Create new markets** (Create Market button in navbar)
- ✅ **Resolve existing markets** (on market detail pages)
- ✅ **Preview resolution outcomes** (see payouts before resolving)
- ✅ **Cancel all open orders** (automatic when resolving)

## Troubleshooting

If the resolution panel doesn't appear:
1. Make sure you're logged in as the admin user
2. Check that your user has `is_admin = true` in the database
3. Check the server logs for any errors

The market resolution functionality is now fully working! You can create markets, trade on them, and then resolve them to see the complete prediction market flow in action. 
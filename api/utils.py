import os
import sys
from flask import current_app
from datetime import datetime, timezone
import uuid
from supabase import create_client, Client

# Add orderbook folder to Python path
orderbook_path = os.path.join(os.path.dirname(__file__), 'orderbook')
sys.path.append(orderbook_path)

try:
    import orderbook_cpp as ob
    ORDERBOOK_AVAILABLE = True
except ImportError as e:
    ORDERBOOK_AVAILABLE = False
    print(f"Warning: C++ orderbook not available: {e}")

def get_or_create_orderbook(market_id):
    """Get existing orderbook or create new one for market"""
    # Check if we're in a serverless environment (Vercel)
    if not ORDERBOOK_AVAILABLE:
        print("C++ orderbook not available in serverless environment")
        return None
        
    markets = current_app.markets
    supabase = current_app.supabase
    if market_id not in markets:
        try:
            market_resp = supabase.table('markets').select('id').eq('id', market_id).single().execute()
            if market_resp.data:
                markets[market_id] = ob.Orderbook()
            else:
                return None
        except:
            return None
    return markets[market_id]

def bootstrap_market(market_id, initial_probability=0.50):
    """Add initial platform liquidity to new market and persist to DB"""
    try:
        supabase = current_app.supabase
        
        yes_price = initial_probability
        no_price = 1.0 - initial_probability
        spread = 0.05
        yes_buy = max(0.01, min(0.99, yes_price - spread))
        yes_sell = max(0.01, min(0.99, yes_price + spread))
        no_buy = max(0.01, min(0.99, no_price - spread))
        no_sell = max(0.01, min(0.99, no_price + spread))
        
        PLATFORM_USER_ID = "9d626b36-4f08-4f7b-b0ea-036ac880be3e"
        qty = 10000
        
        # Create platform user if doesn't exist
        try:
            supabase.table('users').upsert({
                'id': PLATFORM_USER_ID,
                'username': 'platform',
                'display_name': 'Platform Liquidity',
                'balance': 1000000.0,  # Large balance for platform
                'is_admin': True,
                'created_at': datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            print(f"Platform user creation error (may already exist): {e}")
        
        # Try C++ orderbook if available
        if ORDERBOOK_AVAILABLE:
            try:
                orderbook = get_or_create_orderbook(market_id)
                if orderbook:
                    # Add to C++ orderbook
                    orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Buy, int(yes_buy * 100), qty, PLATFORM_USER_ID, ob.Token.YES)
                    orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Sell, int(yes_sell * 100), qty, PLATFORM_USER_ID, ob.Token.YES)
                    orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Buy, int(no_buy * 100), qty, PLATFORM_USER_ID, ob.Token.NO)
                    orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Sell, int(no_sell * 100), qty, PLATFORM_USER_ID, ob.Token.NO)
            except Exception as e:
                print(f"C++ orderbook bootstrap failed, using database only: {e}")
        
        # Always add to database (works for both local and serverless)
        orders_to_create = [
            # YES token orders
            {
                'id': f"{market_id}-yes-buy-{int(yes_buy*100)}",
                'market_id': market_id,
                'user_id': PLATFORM_USER_ID,
                'side': 'buy',  # Correct lowercase
                'token': 'YES',
                'price': yes_buy,
                'size': qty,
                'filled': 0,
                'status': 'open',
                'created_at': datetime.now(timezone.utc).isoformat()
            },
            {
                'id': f"{market_id}-yes-sell-{int(yes_sell*100)}",
                'market_id': market_id,
                'user_id': PLATFORM_USER_ID,
                'side': 'sell',  # Correct lowercase
                'token': 'YES',
                'price': yes_sell,
                'size': qty,
                'filled': 0,
                'status': 'open',
                'created_at': datetime.now(timezone.utc).isoformat()
            },
            # NO token orders
            {
                'id': f"{market_id}-no-buy-{int(no_buy*100)}",
                'market_id': market_id,
                'user_id': PLATFORM_USER_ID,
                'side': 'buy',  # Correct lowercase
                'token': 'NO',
                'price': no_buy,
                'size': qty,
                'filled': 0,
                'status': 'open',
                'created_at': datetime.now(timezone.utc).isoformat()
            },
            {
                'id': f"{market_id}-no-sell-{int(no_sell*100)}",
                'market_id': market_id,
                'user_id': PLATFORM_USER_ID,
                'side': 'sell',  # Correct lowercase
                'token': 'NO',
                'price': no_sell,
                'size': qty,
                'filled': 0,
                'status': 'open',
                'created_at': datetime.utcnow().isoformat()
            }
        ]
        
        # Insert orders
        for order in orders_to_create:
            try:
                supabase.table('orders').upsert(order).execute()
            except Exception as e:
                print(f"Error inserting bootstrap order: {e}")
        
        # Create platform position to enable selling
        try:
            supabase.table('positions').upsert({
                'user_id': PLATFORM_USER_ID,
                'market_id': market_id,
                'yes_shares': qty * 2,  # For both buy and sell orders
                'no_shares': qty * 2,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            print(f"Error creating platform position: {e}")
        
        print(f"Bootstrapped market {market_id} with initial probability {initial_probability}")
        return True
        
    except Exception as e:
        print(f"Error bootstrapping market: {e}")
        return False

def load_all_orderbooks_from_db():
    """Load all active markets - simplified for serverless compatibility"""
    if not ORDERBOOK_AVAILABLE:
        print("Orderbook C++ extension not available; running in serverless mode.")
        return
    
    try:
        supabase = current_app.supabase
        markets = current_app.markets
        
        # Get all active markets
        markets_resp = supabase.table('markets').select('id').eq('status', 'active').execute()
        active_markets = markets_resp.data if markets_resp.data else []
        
        for market in active_markets:
            market_id = market['id']
            # Create orderbook for this market if not already present
            if market_id not in markets:
                markets[market_id] = ob.Orderbook()
            orderbook = markets[market_id]
            
            # Load all open orders for this market
            orders_resp = supabase.table('orders').select('*').eq('market_id', market_id).eq('status', 'open').execute()
            open_orders = orders_resp.data if orders_resp.data else []
            
            for order in open_orders:
                try:
                    order_type = ob.OrderType.GoodTillCancel
                    # Fix side mapping - try uppercase first
                    side = ob.Side.Buy if order['side'].upper() == 'BUY' else ob.Side.Sell
                    price = int(float(order['price']) * 100)
                    # Handle remaining size vs total size
                    remaining_size = int(float(order['size']) - float(order.get('filled', 0)))
                    if remaining_size <= 0:
                        continue
                    user_id = str(order['user_id'])
                    token = ob.Token.YES if order.get('token', 'YES').upper() == 'YES' else ob.Token.NO
                    orderbook.add_order(order_type, side, price, remaining_size, user_id, token)
                except Exception as e:
                    print(f"Error loading order {order.get('id')}: {e}")
        
        print(f"Loaded orderbooks for {len(active_markets)} markets from DB.")
        
    except Exception as e:
        print(f"Error loading orderbooks from DB: {e}")

# Serverless-compatible order matching (database-only)
def match_orders_database_only(market_id, new_order, supabase):
    """Simple order matching using database only (for serverless environments)"""
    try:
        matches = []
        
        # Get opposite side orders that can match
        opposite_side = 'sell' if new_order['side'] == 'buy' else 'buy'
        token = new_order['token']
        
        # Build query for matching orders
        query = supabase.table('orders').select('*').eq('market_id', market_id).eq('side', opposite_side).eq('token', token).eq('status', 'open')
        
        if new_order['side'] == 'buy':
            # Buy order matches with sell orders at or below our price
            matching_orders_resp = query.lte('price', new_order['price']).order('price').execute()
        else:
            # Sell order matches with buy orders at or above our price
            matching_orders_resp = query.gte('price', new_order['price']).order('price', desc=True).execute()
        
        matching_orders = matching_orders_resp.data if matching_orders_resp.data else []
        
        remaining_size = new_order['size']
        
        for order in matching_orders:
            if remaining_size <= 0:
                break
                
            order_remaining = float(order['size']) - float(order.get('filled', 0))
            if order_remaining <= 0:
                continue
            
            # Calculate match size
            match_size = min(remaining_size, order_remaining)
            match_price = float(order['price'])  # Take maker's price
            
            # Record the match
            matches.append({
                'size': match_size,
                'price': match_price,
                'maker_order_id': order['id'],
                'maker_user_id': order['user_id']
            })
            
            # Update filled amounts
            new_filled = float(order.get('filled', 0)) + match_size
            new_status = 'filled' if new_filled >= float(order['size']) else 'open'
            
            supabase.table('orders').update({
                'filled': new_filled,
                'status': new_status,
                'filled_at': datetime.now(timezone.utc).isoformat() if new_status == 'filled' else None
            }).eq('id', order['id']).execute()
            
            remaining_size -= match_size
        
        return matches, remaining_size
        
    except Exception as e:
        print(f"Error in database-only order matching: {e}")
        return [], new_order['size']

def ensure_user_profile_exists(user_id, supabase_client):
    """Ensure a user profile exists in the database, create if missing"""
    try:
        # Try to get existing user
        user_resp = supabase_client.table('users').select('*').eq('id', user_id).execute()
        
        if user_resp.data and len(user_resp.data) > 0:
            # User exists, return the profile
            return user_resp.data[0]
        
        # User doesn't exist, create default profile
        print(f"Creating default user profile for {user_id}")
        
        # Try to get user email from auth if possible
        user_email = ""
        try:
            # This might not work in all contexts, so we handle the error
            from flask import request, current_app
            user_info = current_app.supabase.auth.get_user(request.cookies.get('access_token'))
            user_email = getattr(user_info, 'user', {}).get('email', '')
        except:
            pass
        
        username = user_email.split('@')[0] if user_email else f'user_{user_id[:8]}'
        
        # Create default profile
        profile_data = {
            'id': user_id,
            'username': username,
            'display_name': username,
            'balance': 1000.00,  # Starting balance
            'total_volume': 0.0,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        insert_resp = supabase_client.table('users').upsert(profile_data).execute()
        
        if insert_resp.data:
            print(f"Successfully created user profile for {user_id}")
            return insert_resp.data[0]
        else:
            print(f"Failed to create user profile for {user_id}")
            return None
            
    except Exception as e:
        print(f"Error ensuring user profile exists: {e}")
        return None
import os
import sys
from flask import current_app

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
    if not ORDERBOOK_AVAILABLE:
        return False
    try:
        orderbook = get_or_create_orderbook(market_id)
        if not orderbook:
            return False
        yes_price = initial_probability
        no_price = 1.0 - initial_probability
        spread = 0.05
        yes_buy = int((yes_price - spread) * 100)
        yes_sell = int((yes_price + spread) * 100)
        no_buy = int((no_price - spread) * 100)
        no_sell = int((no_price + spread) * 100)
        PLATFORM_USER_ID = "9d626b36-4f08-4f7b-b0ea-036ac880be3e"
        qty = 10000
        supabase = current_app.supabase
        # Add YES token orders
        orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Buy, yes_buy, qty, PLATFORM_USER_ID, ob.Token.YES)
        supabase.table('orders').insert({
            'market_id': market_id,
            'side': 'YES',
            'price': yes_buy / 100.0,
            'size': qty,
            'status': 'open',
            'user_id': PLATFORM_USER_ID,
            'token': 'YES',
        }).execute()
        orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Sell, yes_sell, qty, PLATFORM_USER_ID, ob.Token.YES)
        supabase.table('orders').insert({
            'market_id': market_id,
            'side': 'YES',
            'price': yes_sell / 100.0,
            'size': qty,
            'status': 'open',
            'user_id': PLATFORM_USER_ID,
            'token': 'YES',
        }).execute()
        # Add NO token orders
        orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Buy, no_buy, qty, PLATFORM_USER_ID, ob.Token.NO)
        supabase.table('orders').insert({
            'market_id': market_id,
            'side': 'NO',
            'price': no_buy / 100.0,
            'size': qty,
            'status': 'open',
            'user_id': PLATFORM_USER_ID,
            'token': 'NO',
        }).execute()
        orderbook.add_order(ob.OrderType.GoodTillCancel, ob.Side.Sell, no_sell, qty, PLATFORM_USER_ID, ob.Token.NO)
        supabase.table('orders').insert({
            'market_id': market_id,
            'side': 'NO',
            'price': no_sell / 100.0,
            'size': qty,
            'status': 'open',
            'user_id': PLATFORM_USER_ID,
            'token': 'NO',
        }).execute()
        return True
    except Exception as e:
        print(f"Error bootstrapping market: {e}")
        return False

def load_all_orderbooks_from_db():
    """Load all active markets and their open orders from the database into in-memory orderbooks."""
    if not ORDERBOOK_AVAILABLE:
        print("Orderbook C++ extension not available; skipping orderbook load.")
        return
    supabase = current_app.supabase
    markets = current_app.markets
    try:
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
                    side = ob.Side.Buy if order['side'].upper() == 'BUY' else ob.Side.Sell
                    price = int(float(order['price']) * 100)
                    size = int(order['size'])
                    user_id = str(order['user_id'])
                    token = ob.Token.YES if order.get('token', 'YES').upper() == 'YES' else ob.Token.NO
                    orderbook.add_order(order_type, side, price, size, user_id, token)
                except Exception as e:
                    print(f"Error loading order {order.get('id')}: {e}")
        print(f"Loaded orderbooks for {len(active_markets)} markets from DB.")
    except Exception as e:
        print(f"Error loading orderbooks from DB: {e}") 
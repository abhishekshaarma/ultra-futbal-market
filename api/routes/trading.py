from flask import Blueprint, request, jsonify, g, current_app as app
from api.auth import login_required
from api.utils import get_or_create_orderbook, bootstrap_market, ORDERBOOK_AVAILABLE, match_orders_database_only
from datetime import datetime
import uuid

trading_bp = Blueprint('trading', __name__)

@trading_bp.route('/api/markets/<market_id>/orders', methods=['POST'])
@login_required
def place_order(market_id):
    """Place a trading order on a market - serverless compatible"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['side', 'token', 'price', 'size']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate field values
        side = data['side'].lower()
        token = data['token'].upper()
        
        if side not in ['buy', 'sell']:
            return jsonify({'error': 'Side must be buy or sell'}), 400
        
        if token not in ['YES', 'NO']:
            return jsonify({'error': 'Token must be YES or NO'}), 400
        
        try:
            price = float(data['price'])
            size = float(data['size'])
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid price or size format'}), 400
        
        # Validate price range (0.01 to 0.99)
        if not (0.01 <= price <= 0.99):
            return jsonify({'error': 'Price must be between 0.01 and 0.99'}), 400
        
        # Validate size
        if size <= 0:
            return jsonify({'error': 'Size must be positive'}), 400
        
        # Check if market exists and is active
        supabase = app.supabase
        market_resp = supabase.table('markets').select('*').eq('id', market_id).single().execute()
        if not market_resp.data:
            return jsonify({'error': 'Market not found'}), 404
        
        market = market_resp.data
        if market['status'] != 'active':
            return jsonify({'error': 'Market is not active for trading'}), 400
        
        # Check if market has ended
        end_date = datetime.fromisoformat(market['end_date'].replace('Z', '+00:00'))
        if datetime.now() >= end_date:
            return jsonify({'error': 'Market has ended for trading'}), 400
        
        # Get user info
        user_id = g.current_user['id']
        
        # Check user balance for buy orders
        if side == 'buy':
            cost = price * size if token == 'YES' else (1 - price) * size
            
            user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
            if not user_resp.data:
                return jsonify({'error': 'User not found'}), 404
            
            user_balance = float(user_resp.data['balance'])
            if user_balance < cost:
                return jsonify({'error': 'Insufficient balance'}), 400
        
        # For sell orders, check if user has enough shares
        if side == 'sell':
            position_resp = supabase.table('positions').select('*').eq('user_id', user_id).eq('market_id', market_id).single().execute()
            
            if position_resp.data:
                position = position_resp.data
                if token == 'YES':
                    available_shares = float(position.get('yes_shares', 0))
                else:
                    available_shares = float(position.get('no_shares', 0))
                
                if available_shares < size:
                    return jsonify({'error': f'Insufficient {token} shares. You have {available_shares}'}), 400
            else:
                return jsonify({'error': f'No {token} shares to sell'}), 400
        
        # Generate order ID
        order_id = str(uuid.uuid4())
        
        # Create order object for matching
        new_order = {
            'id': order_id,
            'market_id': market_id,
            'user_id': user_id,
            'side': side,
            'token': token,
            'price': price,
            'size': size,
            'filled': 0,
            'status': 'open',
            'created_at': datetime.utcnow().isoformat()
        }
        
        # Try C++ orderbook first, fallback to database matching
        matches = []
        filled_amount = 0
        
        if ORDERBOOK_AVAILABLE:
            try:
                orderbook = get_or_create_orderbook(market_id)
                if orderbook:
                    import orderbook_cpp as ob
                    
                    order_type = ob.OrderType.GoodTillCancel
                    ob_side = ob.Side.Buy if side == 'buy' else ob.Side.Sell
                    ob_token = ob.Token.YES if token == 'YES' else ob.Token.NO
                    price_cents = int(price * 100)
                    
                    cpp_matches = orderbook.add_order(order_type, ob_side, price_cents, int(size), user_id, ob_token)
                    
                    if cpp_matches:
                        for match in cpp_matches:
                            filled_amount += match.size
                            matches.append({
                                'size': match.size,
                                'price': match.price / 100.0,
                                'maker_user_id': match.maker_user_id
                            })
            except Exception as e:
                print(f"C++ orderbook failed, using database matching: {e}")
        
        # If C++ orderbook not available or failed, use database matching
        if not matches and not ORDERBOOK_AVAILABLE:
            db_matches, remaining_size = match_orders_database_only(market_id, new_order, supabase)
            filled_amount = size - remaining_size
            matches = db_matches
        
        # Process trades
        trades = []
        for match in matches:
            trade_data = {
                'market_id': market_id,
                'price': match['price'],
                'size': match['size'],
                'buyer_order_id': order_id if side == 'buy' else match.get('maker_order_id', str(uuid.uuid4())),
                'seller_order_id': order_id if side == 'sell' else match.get('maker_order_id', str(uuid.uuid4())),
                'buyer_id': user_id if side == 'buy' else match['maker_user_id'],
                'seller_id': user_id if side == 'sell' else match['maker_user_id'],
                'token': token,
                'created_at': datetime.utcnow().isoformat()
            }
            
            # Record trade in database
            trade_resp = supabase.table('trades').insert(trade_data).execute()
            
            if trade_resp.data:
                trades.append(trade_resp.data[0])
            
            # Update positions for both users
            update_user_position(user_id, market_id, token, side, match['size'], match['price'], supabase)
            update_user_position(match['maker_user_id'], market_id, token, 'sell' if side == 'buy' else 'buy', match['size'], match['price'], supabase)
            
            # Update user balances
            update_user_balances(user_id, match['maker_user_id'], side, match['size'], match['price'], token, supabase)
        
        # Update order with filled amount
        new_order['filled'] = filled_amount
        remaining_size = size - filled_amount
        
        if remaining_size <= 0:
            new_order['status'] = 'filled'
            new_order['filled_at'] = datetime.utcnow().isoformat()
        
        # Insert order into database
        order_resp = supabase.table('orders').insert(new_order).execute()
        
        if not order_resp.data:
            return jsonify({'error': 'Failed to record order'}), 500
        
        # Deduct cost from user balance for unfilled buy orders
        if side == 'buy' and remaining_size > 0:
            remaining_cost = price * remaining_size if token == 'YES' else (1 - price) * remaining_size
            deduct_user_balance(user_id, remaining_cost, supabase)
            
            # Record transaction
            supabase.table('transactions').insert({
                'user_id': user_id,
                'amount': -remaining_cost,
                'type': 'order_placed',
                'description': f'Placed {side} order for {remaining_size} {token} shares',
                'market_id': market_id,
                'order_id': order_id,
                'created_at': datetime.utcnow().isoformat()
            }).execute()
        
        # Update market statistics
        if trades:
            update_market_stats(market_id, trades, supabase)
        
        return jsonify({
            'success': True,
            'order': order_resp.data[0],
            'trades': trades,
            'filled_amount': filled_amount,
            'remaining_size': remaining_size
        })
        
    except Exception as e:
        return jsonify({'error': f'Order placement failed: {str(e)}'}), 500

# Include all other endpoints from the previous trading blueprint...
# (get_orderbook, cancel_order, get_user_orders, etc.)
# They remain the same as in the previous version

@trading_bp.route('/api/markets/<market_id>/orderbook', methods=['GET'])
def get_orderbook(market_id):
    """Get current orderbook state"""
    try:
        supabase = app.supabase
        
        # Check if market exists
        market_resp = supabase.table('markets').select('id').eq('id', market_id).single().execute()
        if not market_resp.data:
            return jsonify({'error': 'Market not found'}), 404
        
        # Get active orders from database
        orders_resp = supabase.table('orders').select('*').eq('market_id', market_id).eq('status', 'open').execute()
        orders = orders_resp.data if orders_resp.data else []
        
        # Organize orders by token and side
        yes_bids = []
        yes_asks = []
        no_bids = []
        no_asks = []
        
        for order in orders:
            remaining_size = float(order['size']) - float(order.get('filled', 0))
            if remaining_size <= 0:
                continue
                
            order_info = {
                'price': float(order['price']),
                'size': remaining_size,
                'user_id': order['user_id'],
                'order_id': order['id']
            }
            
            if order['token'] == 'YES':
                if order['side'] == 'buy':
                    yes_bids.append(order_info)
                else:
                    yes_asks.append(order_info)
            else:  # NO token
                if order['side'] == 'buy':
                    no_bids.append(order_info)
                else:
                    no_asks.append(order_info)
        
        # Sort orders (bids descending, asks ascending)
        yes_bids.sort(key=lambda x: x['price'], reverse=True)
        yes_asks.sort(key=lambda x: x['price'])
        no_bids.sort(key=lambda x: x['price'], reverse=True)
        no_asks.sort(key=lambda x: x['price'])
        
        return jsonify({
            'success': True,
            'orderbook': {
                'yes_token': {
                    'bids': yes_bids,
                    'asks': yes_asks
                },
                'no_token': {
                    'bids': no_bids,
                    'asks': no_asks
                }
            }
        })
            
    except Exception as e:
        return jsonify({'error': f'Failed to get orderbook: {str(e)}'}), 500

# Helper functions remain the same as previous version
def update_user_position(user_id, market_id, token, side, size, price, supabase):
    """Update user position after a trade"""
    try:
        # Get existing position
        position_resp = supabase.table('positions').select('*').eq('user_id', user_id).eq('market_id', market_id).single().execute()
        
        if position_resp.data:
            # Update existing position
            position = position_resp.data
            yes_shares = float(position.get('yes_shares', 0))
            no_shares = float(position.get('no_shares', 0))
            
            if side == 'buy':
                if token == 'YES':
                    yes_shares += size
                else:
                    no_shares += size
            else:  # sell
                if token == 'YES':
                    yes_shares -= size
                else:
                    no_shares -= size
            
            supabase.table('positions').update({
                'yes_shares': max(0, yes_shares),
                'no_shares': max(0, no_shares),
                'updated_at': datetime.utcnow().isoformat()
            }).eq('user_id', user_id).eq('market_id', market_id).execute()
        else:
            # Create new position
            yes_shares = size if (side == 'buy' and token == 'YES') else 0
            no_shares = size if (side == 'buy' and token == 'NO') else 0
            
            supabase.table('positions').insert({
                'user_id': user_id,
                'market_id': market_id,
                'yes_shares': yes_shares,
                'no_shares': no_shares,
                'updated_at': datetime.utcnow().isoformat()
            }).execute()
            
    except Exception as e:
        print(f"Error updating user position: {e}")

def update_user_balances(taker_id, maker_id, taker_side, size, price, token, supabase):
    """Update user balances after a trade"""
    try:
        trade_value = price * size if token == 'YES' else (1 - price) * size
        
        if taker_side == 'buy':
            # Maker (seller) receives payment
            maker_resp = supabase.table('users').select('balance').eq('id', maker_id).single().execute()
            if maker_resp.data:
                maker_balance = float(maker_resp.data['balance'])
                supabase.table('users').update({
                    'balance': maker_balance + trade_value
                }).eq('id', maker_id).execute()
        else:
            # Taker (seller) receives payment
            taker_resp = supabase.table('users').select('balance').eq('id', taker_id).single().execute()
            if taker_resp.data:
                taker_balance = float(taker_resp.data['balance'])
                supabase.table('users').update({
                    'balance': taker_balance + trade_value
                }).eq('id', taker_id).execute()
                
    except Exception as e:
        print(f"Error updating user balances: {e}")

def deduct_user_balance(user_id, amount, supabase):
    """Deduct amount from user balance"""
    try:
        user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
        if user_resp.data:
            current_balance = float(user_resp.data['balance'])
            new_balance = current_balance - amount
            supabase.table('users').update({
                'balance': new_balance
            }).eq('id', user_id).execute()
    except Exception as e:
        print(f"Error deducting user balance: {e}")

def update_market_stats(market_id, trades, supabase):
    """Update market statistics after trades"""
    try:
        # Calculate total volume from trades
        total_trade_volume = sum(float(trade['size']) * float(trade['price']) for trade in trades)
        
        # Get latest trade price for market price update
        latest_trade = trades[-1]
        latest_price = float(latest_trade['price'])
        
        # Update market
        market_resp = supabase.table('markets').select('total_volume').eq('id', market_id).single().execute()
        if market_resp.data:
            current_volume = float(market_resp.data.get('total_volume', 0))
            
            # Update market prices based on latest trade
            update_data = {
                'total_volume': current_volume + total_trade_volume
            }
            
            # Update prices based on the token that was traded
            if latest_trade.get('token') == 'YES':
                update_data['yes_price'] = latest_price
                update_data['no_price'] = 1 - latest_price
            else:
                update_data['no_price'] = latest_price
                update_data['yes_price'] = 1 - latest_price
            
            supabase.table('markets').update(update_data).eq('id', market_id).execute()
            
    except Exception as e:
        print(f"Error updating market stats: {e}")
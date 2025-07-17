from flask import Blueprint, request, jsonify, g, current_app as app
from api.auth import login_required
from api.utils import get_or_create_orderbook, bootstrap_market, ORDERBOOK_AVAILABLE
from datetime import datetime, timezone
import uuid

trading_bp = Blueprint('trading', __name__)

@trading_bp.route('/api/markets/<market_id>/orders', methods=['POST'])
@login_required
def place_order(market_id):
    """Place a trading order on a market"""
    if not ORDERBOOK_AVAILABLE:
        return jsonify({'error': 'Trading temporarily unavailable'}), 503
    
    try:
        data = request.get_json()
        
        # Accept both 'size' and 'quantity'
        size = data.get('size') or data.get('quantity')
        
        # Validate required fields
        required_fields = ['side', 'type', 'price']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        if not size:
            return jsonify({'error': 'Missing required field: size'}), 400
        
        # Validate field values
        side = data['side'].lower()
        side_db = side.upper()  # For DB storage, must match enum: 'BUY' or 'SELL'
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
        if datetime.now(timezone.utc) >= end_date:
            return jsonify({'error': 'Market has ended for trading'}), 400
        
        # Get user info
        user = g.current_user
        user_id = getattr(user, 'id', None) if user else None
        if not user_id:
            return jsonify({'error': 'User not found'}), 401
        
        # Check user balance for buy orders
        if side == 'buy':
            cost = price * size if token == 'YES' else (1 - price) * size
            user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
            if not user_resp.data:
                # Create user row if missing
                user_email = getattr(user, 'email', None)
                username = user_email.split('@')[0] if user_email else f'user_{str(user_id)[:8]}'
                supabase.table('users').insert({
                    'id': user_id,
                    'username': username,
                    'balance': 1000.0,
                    'created_at': datetime.utcnow().isoformat()
                }).execute()
                user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
                if not user_resp.data:
                    return jsonify({'error': 'User not found and could not be created'}), 404
            user_balance = float(user_resp.data['balance'])
            if user_balance < cost:
                return jsonify({'error': 'Insufficient balance'}), 400
        
        # For sell orders, check if user has enough shares
        if side == 'sell':
            position_resp = supabase.table('positions').select('*').eq('user_id', user_id).eq('market_id', market_id).execute()
            positions = position_resp.data if position_resp.data else []
            if not positions:
                return jsonify({'error': f'No {token} shares to sell'}), 400
            position = positions[0]
            if token == 'YES':
                available_shares = float(position.get('yes_shares', 0))
            else:
                available_shares = float(position.get('no_shares', 0))
            if available_shares < size:
                return jsonify({'error': f'Insufficient {token} shares. You have {available_shares}'}), 400
        
        # Get or create orderbook
        orderbook = get_or_create_orderbook(market_id)
        if not orderbook:
            return jsonify({'error': 'Failed to access market orderbook'}), 500
        
        # Convert price to integer (cents)
        price_cents = int(price * 100)
        
        # Map to C++ orderbook types
        import orderbook_cpp as ob
        
        order_type = ob.OrderType.GoodTillCancel
        ob_side = ob.Side.Buy if side == 'buy' else ob.Side.Sell
        ob_token = ob.Token.YES if token == 'YES' else ob.Token.NO
        
        # Generate order ID
        order_id = str(uuid.uuid4())
        
        # Place order in C++ orderbook
        try:
            matches = orderbook.add_order(order_type, ob_side, price_cents, int(size), user_id, ob_token)
            
            # Process any matches that occurred
            filled_amount = 0
            trades = []
            
            if matches:
                for match in matches:
                    trade_size = match.size
                    trade_price = match.price / 100.0
                    filled_amount += trade_size
                    
                    # Determine maker and taker
                    maker_user_id = match.maker_user_id
                    taker_user_id = user_id
                    
                    # Get maker's order ID (we'll need to enhance this)
                    maker_order_id = get_maker_order_id(maker_user_id, market_id, token, supabase)
                    
                    trade_data = {
                        'market_id': market_id,
                        'price': trade_price,
                        'size': trade_size,
                        'buyer_order_id': order_id if side == 'buy' else maker_order_id,
                        'seller_order_id': order_id if side == 'sell' else maker_order_id,
                        'buyer_id': user_id if side == 'buy' else maker_user_id,
                        'seller_id': user_id if side == 'sell' else maker_user_id,
                        'token': token,
                        'created_at': datetime.utcnow().isoformat()
                    }
                    
                    # Record trade in database
                    trade_resp = supabase.table('trades').insert(trade_data).execute()
                    
                    trades.append({
                        'price': trade_price,
                        'size': trade_size,
                        'maker_user_id': maker_user_id,
                        'taker_user_id': user_id,
                        'timestamp': datetime.utcnow().isoformat()
                    })
                    
                    # Update positions for both users
                    update_user_position(user_id, market_id, token, side, trade_size, trade_price, supabase)
                    update_user_position(maker_user_id, market_id, token, 'sell' if side == 'buy' else 'buy', trade_size, trade_price, supabase)
                    
                    # Update user balances
                    update_user_balances(user_id, maker_user_id, side, trade_size, trade_price, token, supabase)
            
            remaining_size = size - filled_amount
            order_status = 'filled' if remaining_size == 0 else ('open' if filled_amount == 0 else 'open')
            
            # Record order in database
            order_data = {
                'id': order_id,
                'market_id': market_id,
                'user_id': user_id,
                'side': side_db,
                'token': token,
                'price': price,
                'size': size,
                'filled': filled_amount,
                'status': order_status,
                'created_at': datetime.utcnow().isoformat()
            }
            
            if filled_amount > 0:
                order_data['filled_at'] = datetime.utcnow().isoformat()
            
            order_resp = supabase.table('orders').insert(order_data).execute()
            
            if not order_resp.data:
                return jsonify({'error': 'Failed to record order'}), 500
            
            # Deduct cost from user balance for buy orders (remaining amount)
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
            
            # Update market volume and prices
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
            return jsonify({'error': f'Failed to place order: {str(e)}'}), 500
        
    except Exception as e:
        return jsonify({'error': f'Order placement failed: {str(e)}'}), 500

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

@trading_bp.route('/api/markets/<market_id>/cancel/<order_id>', methods=['DELETE'])
@login_required
def cancel_order(market_id, order_id):
    """Cancel an order"""
    try:
        supabase = app.supabase
        user_id = g.current_user['id']
        
        # Get order details
        order_resp = supabase.table('orders').select('*').eq('id', order_id).eq('user_id', user_id).single().execute()
        if not order_resp.data:
            return jsonify({'error': 'Order not found or not owned by user'}), 404
        
        order = order_resp.data
        
        # Check if order is cancellable
        if order['status'] != 'open':
            return jsonify({'error': 'Order cannot be cancelled'}), 400
        
        # Cancel in C++ orderbook
        if ORDERBOOK_AVAILABLE:
            orderbook = get_or_create_orderbook(market_id)
            if orderbook:
                try:
                    import orderbook_cpp as ob
                    # Note: This assumes your C++ orderbook has a cancel method
                    # You may need to adjust based on your actual interface
                    orderbook.cancel_order(order_id)
                except Exception as e:
                    print(f"Failed to cancel order in C++ orderbook: {e}")
        
        # Update order status in database
        update_resp = supabase.table('orders').update({
            'status': 'cancelled'
        }).eq('id', order_id).execute()
        
        if not update_resp.data:
            return jsonify({'error': 'Failed to cancel order'}), 500
        
        # Refund user balance for buy orders
        if order['side'] == 'buy':
            remaining_size = float(order['size']) - float(order.get('filled', 0))
            if remaining_size > 0:
                token = order['token']
                price = float(order['price'])
                refund_amount = price * remaining_size if token == 'YES' else (1 - price) * remaining_size
                
                # Add refund to user balance
                user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
                if user_resp.data:
                    current_balance = float(user_resp.data['balance'])
                    new_balance = current_balance + refund_amount
                    
                    supabase.table('users').update({
                        'balance': new_balance
                    }).eq('id', user_id).execute()
                    
                    # Record refund transaction
                    supabase.table('transactions').insert({
                        'user_id': user_id,
                        'amount': refund_amount,
                        'type': 'order_cancelled',
                        'description': f'Order cancellation refund',
                        'market_id': market_id,
                        'order_id': order_id,
                        'created_at': datetime.utcnow().isoformat()
                    }).execute()
        
        return jsonify({
            'success': True,
            'message': f'Order {order_id} cancelled',
            'order': update_resp.data[0]
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to cancel order: {str(e)}'}), 500

@trading_bp.route('/api/user/orders', methods=['GET'])
@login_required
def get_user_orders():
    """Get all orders for the current user"""
    try:
        supabase = app.supabase
        user_id = g.current_user['id']
        
        # Get query parameters
        market_id = request.args.get('market_id')
        status = request.args.get('status')
        
        # Build query
        query = supabase.table('orders').select('*').eq('user_id', user_id)
        
        if market_id:
            query = query.eq('market_id', market_id)
        
        if status:
            query = query.eq('status', status)
        
        # Execute query with ordering
        orders_resp = query.order('created_at', desc=True).execute()
        orders = orders_resp.data if orders_resp.data else []
        
        return jsonify({
            'success': True,
            'orders': orders
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to get user orders: {str(e)}'}), 500

@trading_bp.route('/api/user/positions', methods=['GET'])
@login_required
def get_user_positions():
    """Get all positions for the current user"""
    try:
        supabase = app.supabase
        user_id = g.current_user['id']
        
        market_id = request.args.get('market_id')
        
        # Build query
        query = supabase.table('positions').select('*').eq('user_id', user_id)
        
        if market_id:
            query = query.eq('market_id', market_id)
        
        positions_resp = query.execute()
        positions = positions_resp.data if positions_resp.data else []
        
        return jsonify({
            'success': True,
            'positions': positions
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to get user positions: {str(e)}'}), 500

@trading_bp.route('/api/user/balance', methods=['GET'])
@login_required
def get_user_balance():
    """Get current user balance"""
    try:
        supabase = app.supabase
        user_id = g.current_user['id']
        
        user_resp = supabase.table('users').select('balance, total_volume').eq('id', user_id).single().execute()
        if not user_resp.data:
            return jsonify({'error': 'User not found'}), 404
        
        return jsonify({
            'success': True,
            'balance': float(user_resp.data['balance']),
            'total_volume': float(user_resp.data.get('total_volume', 0))
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to get user balance: {str(e)}'}), 500

@trading_bp.route('/api/markets/<market_id>/trades', methods=['GET'])
def get_market_trades(market_id):
    """Get recent trades for a market"""
    try:
        supabase = app.supabase
        
        # Get query parameters
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        # Get trades for this market
        trades_resp = supabase.table('trades').select('*').eq('market_id', market_id).order('created_at', desc=True).limit(limit).offset(offset).execute()
        trades = trades_resp.data if trades_resp.data else []
        
        return jsonify({
            'success': True,
            'trades': trades
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to get market trades: {str(e)}'}), 500

@trading_bp.route('/api/test/add_sample_orders')
def add_sample_orders():
    """Add some sample orders for testing"""
    try:
        if not ORDERBOOK_AVAILABLE:
            return jsonify({'error': 'Orderbook not available'}), 503
        
        supabase = app.supabase
        
        # Get first active market
        markets_resp = supabase.table('markets').select('*').eq('status', 'active').limit(1).execute()
        if not markets_resp.data:
            return jsonify({'error': 'No active markets found'}), 404
        
        market_id = markets_resp.data[0]['id']
        
        # Sample orders data
        sample_orders = [
            {'side': 'buy', 'token': 'YES', 'price': 0.45, 'size': 100},
            {'side': 'buy', 'token': 'YES', 'price': 0.40, 'size': 150},
            {'side': 'sell', 'token': 'YES', 'price': 0.55, 'size': 200},
            {'side': 'sell', 'token': 'YES', 'price': 0.60, 'size': 100},
            {'side': 'buy', 'token': 'NO', 'price': 0.45, 'size': 120},
            {'side': 'buy', 'token': 'NO', 'price': 0.40, 'size': 180},
            {'side': 'sell', 'token': 'NO', 'price': 0.55, 'size': 150},
            {'side': 'sell', 'token': 'NO', 'price': 0.60, 'size': 90},
        ]
        
        # Test user ID
        TEST_USER_ID = "test-user-" + str(uuid.uuid4())[:8]
        
        # Create test user with balance
        supabase.table('users').upsert({
            'id': TEST_USER_ID,
            'username': f'testuser_{TEST_USER_ID[:8]}',
            'balance': 10000.0,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        orderbook = get_or_create_orderbook(market_id)
        if not orderbook:
            return jsonify({'error': 'Failed to get orderbook'}), 500
        
        import orderbook_cpp as ob
        
        orders_added = []
        
        for order_data in sample_orders:
            order_id = str(uuid.uuid4())
            
            order_type = ob.OrderType.GoodTillCancel
            ob_side = ob.Side.Buy if order_data['side'] == 'buy' else ob.Side.Sell
            ob_token = ob.Token.YES if order_data['token'] == 'YES' else ob.Token.NO
            price_cents = int(order_data['price'] * 100)
            
            # Add to C++ orderbook
            orderbook.add_order(order_type, ob_side, price_cents, int(order_data['size']), TEST_USER_ID, ob_token)
            
            # Add to database
            db_order = {
                'id': order_id,
                'market_id': market_id,
                'user_id': TEST_USER_ID,
                'side': order_data['side'].upper(), # Ensure uppercase for DB
                'token': order_data['token'],
                'price': order_data['price'],
                'size': order_data['size'],
                'filled': 0,
                'status': 'open',
                'created_at': datetime.utcnow().isoformat()
            }
            
            supabase.table('orders').insert(db_order).execute()
            orders_added.append(db_order)
        
        return jsonify({
            'success': True,
            'message': f'Added {len(orders_added)} sample orders',
            'orders': orders_added
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to add sample orders: {str(e)}'}), 500

@trading_bp.route('/api/test/bootstrap_market/<market_id>')
def bootstrap_test_market(market_id):
    """Bootstrap a specific market with initial liquidity"""
    try:
        # Check if market exists
        supabase = app.supabase
        market_resp = supabase.table('markets').select('*').eq('id', market_id).single().execute()
        if not market_resp.data:
            return jsonify({'error': 'Market not found'}), 404
        
        # Bootstrap the market
        initial_prob = float(request.args.get('initial_probability', 0.5))
        
        if bootstrap_market(market_id, initial_prob):
            return jsonify({
                'success': True,
                'message': f'Market {market_id} bootstrapped with {initial_prob} initial probability'
            })
        else:
            return jsonify({'error': 'Failed to bootstrap market'}), 500
            
    except Exception as e:
        return jsonify({'error': f'Failed to bootstrap market: {str(e)}'}), 500

# Helper Functions

def get_maker_order_id(maker_user_id, market_id, token, supabase):
    """Get the order ID for the maker (simplified - you may need better logic)"""
    try:
        order_resp = supabase.table('orders').select('id').eq('user_id', maker_user_id).eq('market_id', market_id).eq('token', token).eq('status', 'open').limit(1).execute()
        if order_resp.data:
            return order_resp.data[0]['id']
        return str(uuid.uuid4())  # Fallback
    except:
        return str(uuid.uuid4())  # Fallback

def update_user_position(user_id, market_id, token, side, size, price, supabase):
    """Update user position after a trade"""
    try:
        # Get existing position
        position_resp = supabase.table('positions').select('*').eq('user_id', user_id).eq('market_id', market_id).execute()
        positions = position_resp.data if position_resp.data else []
        if positions:
            # Update existing position
            position = positions[0]
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
            # Taker (buyer) pays, maker (seller) receives
            # Taker already had balance deducted when order was placed partially
            
            # Maker receives payment
            maker_resp = supabase.table('users').select('balance').eq('id', maker_id).single().execute()
            if maker_resp.data:
                maker_balance = float(maker_resp.data['balance'])
                supabase.table('users').update({
                    'balance': maker_balance + trade_value
                }).eq('id', maker_id).execute()
        else:
            # Taker (seller) receives, maker (buyer) pays
            # Maker already had balance deducted when their order was placed
            
            # Taker receives payment
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
        total_trade_volume = sum(trade['size'] * trade['price'] for trade in trades)
        
        # Get latest trade price for market price update
        latest_trade = trades[-1]
        latest_price = latest_trade['price']
        
        # Update market
        market_resp = supabase.table('markets').select('total_volume').eq('id', market_id).single().execute()
        if market_resp.data:
            current_volume = float(market_resp.data.get('total_volume', 0))
            
            # Update market prices based on latest trade
            # This is a simplified approach - you might want more sophisticated pricing
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
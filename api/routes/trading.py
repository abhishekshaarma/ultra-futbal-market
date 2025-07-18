from flask import Blueprint, request, jsonify, g, current_app as app
from api.auth import login_required
from api.utils import get_or_create_orderbook, bootstrap_market, ORDERBOOK_AVAILABLE, match_orders_database_only
from datetime import datetime, timezone
import uuid

trading_bp = Blueprint('trading', __name__)

def get_current_user_id():
    """Helper function to extract user ID from g.current_user"""
    current_user = g.current_user
    print(f"DEBUG: Current user object: {current_user}")
    print(f"DEBUG: Current user type: {type(current_user)}")
    
    if hasattr(current_user, 'id'):
        print(f"DEBUG: Using current_user.id: {current_user.id}")
        return current_user.id
    elif isinstance(current_user, dict):
        print(f"DEBUG: Using current_user['id']: {current_user['id']}")
        return current_user['id']
    else:
        print(f"DEBUG: Converting current_user to string: {str(current_user)}")
        return str(current_user)

def ensure_user_profile_exists(user_id, email=None, supabase_client=None):
    """
    Ensure a user profile exists in the users table.
    Creates one if it doesn't exist.
    """
    if not supabase_client:
        supabase_client = app.supabase
    
    try:
        # Check if user profile already exists
        user_resp = supabase_client.table('users').select('*').eq('id', user_id).execute()
        
        if user_resp.data and len(user_resp.data) > 0:
            # User exists, return the profile
            return user_resp.data[0]
        
        # User doesn't exist, create profile
        print(f"Creating user profile for {user_id}")
        
        # Generate username from email or user_id
        if email:
            username = email.split('@')[0]
        else:
            # Try to get email from auth.users
            try:
                auth_user = supabase_client.table('auth.users').select('email').eq('id', user_id).single().execute()
                if auth_user.data and auth_user.data.get('email'):
                    username = auth_user.data['email'].split('@')[0]
                else:
                    username = f'user_{user_id[:8]}'
            except:
                username = f'user_{user_id[:8]}'
        
        # Create the user profile
        profile_data = {
            'id': user_id,
            'username': username,
            'display_name': username,
            'balance': 1000.00,
            'total_volume': 0.0,
            'is_admin': False,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        insert_resp = supabase_client.table('users').insert(profile_data).execute()
        
        if insert_resp.data:
            print(f"Successfully created user profile for {user_id}")
            return insert_resp.data[0]
        else:
            print(f"Failed to create user profile for {user_id}")
            return None
            
    except Exception as e:
        print(f"Error ensuring user profile exists: {e}")
        return None

def create_default_user_profile(user_id, supabase):
    """Create a default user profile with starting balance"""
    try:
        # Get user info from auth
        user = getattr(g, 'current_user', None)
        user_email = getattr(user, 'email', None)
        username = user_email.split('@')[0] if user_email else f'user_{str(user_id)[:8]}'
        
        # Use admin client to bypass RLS
        admin_client = getattr(app, 'supabase_admin', supabase)
        
        # Create user profile
        user_data = {
            'id': user_id,  # Changed back to 'id'
            'username': username,
            'display_name': username,
            'balance': 1000.00,
            'total_volume': 0.0,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        result = admin_client.table('users').upsert(user_data).execute()
        print(f"Successfully created user profile for {user_id}")
        return True
    except Exception as e:
        print(f"Failed to create user profile: {e}")
        return False

@trading_bp.route('/api/markets/<market_id>/orders', methods=['POST'])
@login_required
def place_order(market_id):
    """Place a trading order on a market - serverless compatible"""
    try:
        data = request.get_json()
        print(f"DEBUG: Received data: {data}")
        print(f"DEBUG: Market ID: {market_id}")
        
        # Validate required fields
        required_fields = ['side', 'token', 'price', 'size']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate field values
        side = data['side'].lower()  # buy/sell direction
        token = data['token'].upper()  # YES/NO token type
        
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
        
        supabase = app.supabase
        
        # Check if market exists and is active - ADD DEBUG
        print(f"DEBUG: Looking up market {market_id}")
        try:
            market_resp = supabase.table('markets').select('*').eq('id', market_id).single().execute()
            print(f"DEBUG: Market response: {market_resp}")
            
            if not market_resp.data:
                return jsonify({'error': 'Market not found'}), 404
            
            market = market_resp.data
            print(f"DEBUG: Found market: {market['title']}, status: {market['status']}")
            
        except Exception as e:
            print(f"DEBUG: Market lookup failed: {e}")
            return jsonify({'error': f'Market lookup failed: {str(e)}'}), 500
        
        if market['status'] != 'active':
            return jsonify({'error': 'Market is not active for trading'}), 400
        
        # Check if market has ended
        end_date = datetime.fromisoformat(market['end_date'].replace('Z', '+00:00'))
        if datetime.now(timezone.utc) >= end_date:
            return jsonify({'error': 'Market has ended for trading'}), 400
        
        # Get user info - ADD DEBUG
        user_id = get_current_user_id()
        print(f"DEBUG: Current user ID: {user_id}")
        
        # Check user balance for buy orders - ADD DEBUG
        if side == 'buy':
            cost = price * size if token == 'YES' else (1 - price) * size
            print(f"DEBUG: Buy order cost: {cost}")
            
            try:
                # First, let's check if user exists at all
                user_check_resp = supabase.table('users').select('*').eq('id', user_id).execute()
                print(f"DEBUG: User check response: {user_check_resp}")
                print(f"DEBUG: User check data: {user_check_resp.data}")
                print(f"DEBUG: User check count: {len(user_check_resp.data) if user_check_resp.data else 0}")
                
                user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
                print(f"DEBUG: User balance response: {user_resp}")
                
                if user_resp.data:
                    user_balance = float(user_resp.data['balance'])
                    print(f"DEBUG: User balance: {user_balance}")
                    
                    if user_balance < cost:
                        return jsonify({'error': 'Insufficient balance'}), 400
                else:
                    # User doesn't exist, create default profile
                    print(f"DEBUG: User {user_id} not found, creating default profile")
                    create_default_user_profile(user_id, supabase)
                    user_balance = 1000.00
                    print(f"DEBUG: Created user with default balance: {user_balance}")
                    
                    if user_balance < cost:
                        return jsonify({'error': 'Insufficient balance'}), 400
                    
            except Exception as e:
                print(f"DEBUG: User balance lookup failed: {e}")
                # Try to create user profile and retry
                try:
                    print(f"DEBUG: Attempting to create user profile for {user_id}")
                    create_default_user_profile(user_id, supabase)
                    user_balance = 1000.00
                    print(f"DEBUG: Created user with default balance: {user_balance}")
                    
                    if user_balance < cost:
                        return jsonify({'error': 'Insufficient balance'}), 400
                except Exception as create_error:
                    print(f"Failed to create user profile: {create_error}")
                    return jsonify({
                        'error': f'User profile not found. Please contact support to create your profile. User ID: {user_id}'
                    }), 500
        
        # For sell orders, check if user has enough shares - ADD DEBUG
        if side == 'sell':
            print(f"DEBUG: Checking sell order for {token} shares")
            try:
                # Use execute() without single() first to see if position exists
                position_resp = supabase.table('positions').select('*').eq('user_id', user_id).eq('market_id', market_id).execute()
                print(f"DEBUG: Position response: {position_resp}")
                
                if position_resp.data and len(position_resp.data) > 0:
                    position = position_resp.data[0]  # Get first position
                    if token == 'YES':
                        available_shares = float(position.get('yes_shares', 0))
                    else:
                        available_shares = float(position.get('no_shares', 0))
                    
                    print(f"DEBUG: Available {token} shares: {available_shares}")
                    
                    if available_shares < size:
                        return jsonify({'error': f'Insufficient {token} shares. You have {available_shares}'}), 400
                else:
                    print(f"DEBUG: No position found for user {user_id} in market {market_id}")
                    return jsonify({'error': f'No {token} shares to sell'}), 400
                    
            except Exception as e:
                print(f"DEBUG: Position lookup failed: {e}")
                return jsonify({'error': f'Position lookup failed: {str(e)}'}), 500
        
        # Generate order ID
        order_id = str(uuid.uuid4())
        print(f"DEBUG: Generated order ID: {order_id}")
        
        # Create order object for matching
        db_side = token.upper()  # 'YES' or 'NO' goes to side column
        db_token = side.upper()  # 'BUY' or 'SELL' goes to token column
        
        new_order = {
            'id': order_id,
            'market_id': market_id,
            'user_id': user_id,
            'side': db_side,      # 'YES' or 'NO' (token type)
            'token': db_token,    # 'BUY' or 'SELL' (direction)
            'price': price,
            'size': size,
            'filled': 0,
            'status': 'open',
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        print(f"DEBUG: Order to insert: {new_order}")
        
        # Skip C++ orderbook for now to simplify debugging
        matches = []
        filled_amount = 0
        
        # Update order with filled amount
        new_order['filled'] = filled_amount
        remaining_size = size - filled_amount
        
        if remaining_size <= 0:
            new_order['status'] = 'filled'
            new_order['filled_at'] = datetime.now(timezone.utc).isoformat()
        
        # Insert order into database
        try:
            print(f"DEBUG: Inserting order into database...")
            order_resp = supabase.table('orders').insert(new_order).execute()
            print(f"DEBUG: Order insert response: {order_resp}")
            
            if not order_resp.data:
                return jsonify({'error': 'Failed to record order'}), 500
        except Exception as e:
            print(f"DEBUG: Database insert failed: {e}")
            return jsonify({'error': f'Database insert failed: {str(e)}'}), 500
        
        # Deduct cost from user balance for unfilled buy orders
        if side == 'buy' and remaining_size > 0:
            remaining_cost = price * remaining_size if token == 'YES' else (1 - price) * remaining_size
            print(f"DEBUG: Deducting {remaining_cost} from user balance")
            
            # Use admin client for balance deduction and transaction recording
            admin_client = getattr(app, 'supabase_admin', supabase)
            
            try:
                # Deduct from balance
                user_resp = admin_client.table('users').select('balance').eq('id', user_id).single().execute()
                if user_resp.data:
                    current_balance = float(user_resp.data['balance'])
                    new_balance = current_balance - remaining_cost
                    
                    admin_client.table('users').update({
                        'balance': new_balance
                    }).eq('id', user_id).execute()
                    print(f"DEBUG: Updated user balance from {current_balance} to {new_balance}")
                else:
                    print(f"DEBUG: User not found for balance update")
            except Exception as e:
                print(f"Error deducting user balance: {e}")
            
            # Record transaction
            try:
                admin_client.table('transactions').insert({
                    'user_id': user_id,
                    'amount': -remaining_cost,
                    'type': 'order_placed',
                    'description': f'Placed {side} order for {remaining_size} {token} shares',
                    'market_id': market_id,
                    'order_id': order_id,
                    'created_at': datetime.now(timezone.utc).isoformat()
                }).execute()
                print(f"DEBUG: Transaction recorded successfully")
            except Exception as e:
                print(f"Error recording transaction: {e}")
        
        return jsonify({
            'success': True,
            'order': order_resp.data[0],
            'trades': [],
            'filled_amount': filled_amount,
            'remaining_size': remaining_size
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
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
        # Based on your schema: side = token type (YES/NO), token = direction (BUY/SELL)
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
            
            # side = token type (YES/NO), token = direction (BUY/SELL)
            if order['side'] == 'YES':  # YES token orders
                if order['token'] == 'BUY':  # Buying YES tokens
                    yes_bids.append(order_info)
                else:  # Selling YES tokens
                    yes_asks.append(order_info)
            else:  # NO token orders (side == 'NO')
                if order['token'] == 'BUY':  # Buying NO tokens
                    no_bids.append(order_info)
                else:  # Selling NO tokens
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
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to get orderbook: {str(e)}'}), 500

@trading_bp.route('/api/markets/<market_id>/cancel/<order_id>', methods=['DELETE'])
@login_required
def cancel_order(market_id, order_id):
    """Cancel an order"""
    try:
        supabase = app.supabase
        
        # Get user info
        user_id = get_current_user_id()
        
        # Get order details
        order_resp = supabase.table('orders').select('*').eq('id', order_id).eq('user_id', user_id).single().execute()
        if not order_resp.data:
            return jsonify({'error': 'Order not found or not owned by user'}), 404
        
        order = order_resp.data
        
        # Check if order is cancellable
        if order['status'] != 'open':
            return jsonify({'error': 'Order cannot be cancelled'}), 400
        
        # Cancel in C++ orderbook if available
        if ORDERBOOK_AVAILABLE:
            orderbook = get_or_create_orderbook(market_id)
            if orderbook:
                try:
                    import orderbook_cpp as ob
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
        # Remember: side = token type (YES/NO), token = direction (BUY/SELL)
        if order['token'] == 'BUY':  # This was a buy order
            remaining_size = float(order['size']) - float(order.get('filled', 0))
            if remaining_size > 0:
                token_type = order['side']  # YES or NO
                price = float(order['price'])
                refund_amount = price * remaining_size if token_type == 'YES' else (1 - price) * remaining_size
                
                # Add refund to user balance
                user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
                if user_resp.data:
                    current_balance = float(user_resp.data['balance'])
                    new_balance = current_balance + refund_amount
                    
                    supabase.table('users').update({
                        'balance': new_balance
                    }).eq('id', user_id).execute()
                    
                    # Record refund transaction
                    admin_client = getattr(app, 'supabase_admin', supabase)
                    try:
                        admin_client.table('transactions').insert({
                            'user_id': user_id,
                            'amount': refund_amount,
                            'type': 'order_cancelled',
                            'description': f'Order cancellation refund',
                            'market_id': market_id,
                            'order_id': order_id,
                            'created_at': datetime.now(timezone.utc).isoformat()
                        }).execute()
                    except Exception as e:
                        print(f"Error recording refund transaction: {e}")
        
        return jsonify({
            'success': True,
            'message': f'Order {order_id} cancelled',
            'order': update_resp.data[0]
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to cancel order: {str(e)}'}), 500

@trading_bp.route('/api/user/orders', methods=['GET'])
@login_required
def get_user_orders():
    """Get all orders for the current user"""
    try:
        supabase = app.supabase
        
        # Get user info
        user_id = get_current_user_id()
        
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
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to get user orders: {str(e)}'}), 500

@trading_bp.route('/api/user/positions', methods=['GET'])
@login_required
def get_user_positions():
    """Get all positions for the current user"""
    try:
        supabase = app.supabase
        
        # Get user info
        user_id = get_current_user_id()
        
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
        
        # Get user info
        user_id = get_current_user_id()
        
        try:
            user_resp = supabase.table('users').select('balance, total_volume').eq('id', user_id).single().execute()
            if user_resp.data:
                return jsonify({
                    'success': True,
                    'balance': float(user_resp.data['balance']),
                    'total_volume': float(user_resp.data.get('total_volume', 0))
                })
        except Exception as e:
            # User doesn't exist in database, create default profile
            print(f"User {user_id} not found in database, creating default profile")
        
        # Create default user profile
        try:
            # Get user email from auth
            user_info = app.supabase.auth.get_user(request.cookies.get('access_token'))
            user_email = getattr(user_info, 'user', {}).get('email', '')
            username = user_email.split('@')[0] if user_email else f'user_{user_id[:8]}'
            
            # Use admin client to bypass RLS policies
            admin_client = getattr(app, 'supabase_admin', supabase)
            
            # Insert default user profile
            admin_client.table('users').insert({
                'id': user_id,  # Changed back to 'id'
                'username': username,
                'display_name': username,
                'balance': 1000.00,  # Starting balance
                'total_volume': 0.0,
                'created_at': datetime.now(timezone.utc).isoformat()
            }).execute()
            
            return jsonify({
                'success': True,
                'balance': 1000.00,
                'total_volume': 0.0
            })
            
        except Exception as create_error:
            print(f"Failed to create user profile: {create_error}")
            # Return default values if creation fails
            return jsonify({
                'success': True,
                'balance': 1000.00,
                'total_volume': 0.0
            })
        
    except Exception as e:
        import traceback; traceback.print_exc()
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
            'id': TEST_USER_ID,  # Changed back to 'id'
            'username': f'testuser_{TEST_USER_ID[:8]}',
            'balance': 10000.0,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        # Create position for test user to enable selling
        supabase.table('positions').upsert({
            'user_id': TEST_USER_ID,
            'market_id': market_id,
            'yes_shares': 1000,
            'no_shares': 1000,
            'updated_at': datetime.utcnow().isoformat()
        }).execute()
        
        orders_added = []
        
        for order_data in sample_orders:
            order_id = str(uuid.uuid4())
            
            # Convert to database format
            db_side = order_data['token'].upper()  # YES/NO goes to side
            db_token = order_data['side'].upper()  # BUY/SELL goes to token
            
            # Add to database
            db_order = {
                'id': order_id,
                'market_id': market_id,
                'user_id': TEST_USER_ID,
                'side': db_side,      # YES/NO
                'token': db_token,    # BUY/SELL
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
        import traceback; traceback.print_exc()
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
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to bootstrap market: {str(e)}'}), 500

# Test endpoint to try different enum values
@trading_bp.route('/api/debug/test-enum', methods=['POST'])
def test_enum_values():
    """Test what enum values work for side column"""
    try:
        supabase = app.supabase
        
        # Get a real market_id and user_id for testing
        markets_resp = supabase.table('markets').select('id').limit(1).execute()
        if not markets_resp.data:
            return jsonify({'error': 'No markets found for testing'}), 400
        
        market_id = markets_resp.data[0]['id']
        
        # Use current user ID
        user_id = get_current_user_id()
        
        test_values = ['buy', 'sell', 'BUY', 'SELL', 'YES', 'NO']
        results = {}
        
        for value in test_values:
            try:
                # Try to insert a test order with this side value
                test_order = {
                    'id': str(uuid.uuid4()),  # Proper UUID
                    'market_id': market_id,   # Real market UUID
                    'user_id': user_id,       # Real user UUID
                    'side': value,            # Test this enum value
                    'price': 0.5,
                    'size': 1,
                    'token': 'YES',
                    'status': 'open',
                    'filled': 0
                }
                
                # This will fail if the enum doesn't accept this value
                insert_resp = supabase.table('orders').insert(test_order).execute()
                results[value] = 'SUCCESS'
                
                # Clean up - delete the test order
                if insert_resp.data:
                    supabase.table('orders').delete().eq('id', test_order['id']).execute()
                
            except Exception as e:
                results[value] = f'FAILED: {str(e)}'
        
        return jsonify({
            'success': True,
            'enum_test_results': results,
            'test_market_id': market_id,
            'test_user_id': user_id
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Test failed: {str(e)}'}), 500

@trading_bp.route('/api/debug/enum-values', methods=['GET'])
def debug_enum_values():
    """Debug endpoint to check what enum values are expected"""
    try:
        supabase = app.supabase
        
        # Try to get existing orders to see what values are used
        orders_resp = supabase.table('orders').select('*').limit(5).execute()
        orders = orders_resp.data if orders_resp.data else []
        
        # Get unique values for each column
        sides = list(set(order.get('side') for order in orders if order.get('side')))
        statuses = list(set(order.get('status') for order in orders if order.get('status')))
        tokens = list(set(order.get('token') for order in orders if order.get('token')))
        
        # Show the actual structure
        sample_order = orders[0] if orders else {}
        
        return jsonify({
            'success': True,
            'existing_sides': sides,
            'existing_statuses': statuses,
            'existing_tokens': tokens,
            'sample_order_structure': sample_order,
            'all_columns': list(sample_order.keys()) if sample_order else []
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Debug failed: {str(e)}'}), 500

@trading_bp.route('/api/debug/users', methods=['GET'])
def debug_users():
    """Debug endpoint to check users in database"""
    try:
        supabase = app.supabase
        
        # Get all users
        users_resp = supabase.table('users').select('*').execute()
        
        # Try to get current user ID, but handle errors gracefully
        try:
            user_id = get_current_user_id()
            current_user_exists = any(user.get('id') == user_id for user in (users_resp.data or []))
        except Exception as auth_error:
            user_id = "ERROR: " + str(auth_error)
            current_user_exists = False
        
        return jsonify({
            'success': True,
            'current_user_id': user_id,
            'total_users': len(users_resp.data) if users_resp.data else 0,
            'users': users_resp.data if users_resp.data else [],
            'current_user_exists': current_user_exists,
            'auth_error': str(auth_error) if 'auth_error' in locals() else None
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to get users: {str(e)}'}), 500

@trading_bp.route('/api/debug/all-users', methods=['GET'])
def debug_all_users():
    """Debug endpoint to check all users in database without auth"""
    try:
        supabase = app.supabase
        
        # Get all users
        users_resp = supabase.table('users').select('*').execute()
        
        return jsonify({
            'success': True,
            'total_users': len(users_resp.data) if users_resp.data else 0,
            'users': users_resp.data if users_resp.data else []
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to get users: {str(e)}'}), 500

@trading_bp.route('/api/debug/create-user-profile', methods=['POST'])
@login_required
def debug_create_user_profile():
    """Debug endpoint to manually create user profile for current user"""
    try:
        current_user_id = get_current_user_id()
        if not current_user_id:
            return jsonify({'error': 'No user ID found'}), 400
        
        # Get user info from auth
        user = getattr(g, 'current_user', None)
        user_email = getattr(user, 'email', None)
        username = user_email.split('@')[0] if user_email else f'user_{str(current_user_id)[:8]}'
        
        # Use admin client to bypass RLS
        admin_client = getattr(app, 'supabase_admin', app.supabase)
        
        # Create user profile
        user_data = {
            'id': current_user_id,  # Changed back to 'id'
            'username': username,
            'display_name': username,
            'balance': 1000.00,
            'total_volume': 0.0,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        result = admin_client.table('users').upsert(user_data).execute()
        
        return jsonify({
            'success': True,
            'message': f'User profile created for {current_user_id}',
            'user_data': user_data,
            'result': result.data if hasattr(result, 'data') else str(result)
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to create user profile: {str(e)}'}), 500

# Helper Functions

def update_user_position(user_id, market_id, token_type, direction, size, price, supabase):
    """
    Update user position after a trade
    token_type: 'YES' or 'NO' 
    direction: 'buy' or 'sell'
    """
    try:
        # Get existing position
        position_resp = supabase.table('positions').select('*').eq('user_id', user_id).eq('market_id', market_id).single().execute()
        
        if position_resp.data:
            # Update existing position
            position = position_resp.data
            yes_shares = float(position.get('yes_shares', 0))
            no_shares = float(position.get('no_shares', 0))
            
            if direction == 'buy':
                if token_type == 'YES':
                    yes_shares += size
                else:
                    no_shares += size
            else:  # sell
                if token_type == 'YES':
                    yes_shares -= size
                else:
                    no_shares -= size
            
            supabase.table('positions').update({
                'yes_shares': max(0, yes_shares),
                'no_shares': max(0, no_shares),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).eq('user_id', user_id).eq('market_id', market_id).execute()
        else:
            # Create new position
            yes_shares = size if (direction == 'buy' and token_type == 'YES') else 0
            no_shares = size if (direction == 'buy' and token_type == 'NO') else 0
            
            supabase.table('positions').insert({
                'user_id': user_id,
                'market_id': market_id,
                'yes_shares': yes_shares,
                'no_shares': no_shares,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).execute()
            
    except Exception as e:
        print(f"Error updating user position: {e}")

def update_user_balances(taker_id, maker_id, taker_direction, size, price, token_type, supabase):
    """
    Update user balances after a trade
    taker_direction: 'buy' or 'sell'
    token_type: 'YES' or 'NO'
    """
    try:
        trade_value = price * size if token_type == 'YES' else (1 - price) * size
        
        if taker_direction == 'buy':
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


# Add this to trading.py temporarily to debug the orderbook endpoint

@trading_bp.route('/api/markets/<market_id>/orderbook-debug', methods=['GET'])
def debug_orderbook(market_id):
    """Debug version of orderbook endpoint"""
    try:
        print(f"DEBUG: Getting orderbook for market {market_id}")
        supabase = app.supabase
        
        # Check if market exists
        print(f"DEBUG: Checking if market {market_id} exists...")
        try:
            market_resp = supabase.table('markets').select('id').eq('id', market_id).single().execute()
            print(f"DEBUG: Market check response: {market_resp}")
            
            if not market_resp.data:
                return jsonify({'error': 'Market not found'}), 404
        except Exception as e:
            print(f"DEBUG: Market check failed: {e}")
            return jsonify({'error': f'Market check failed: {str(e)}'}), 500
        
        # Get active orders from database
        print(f"DEBUG: Getting orders for market {market_id}...")
        try:
            orders_resp = supabase.table('orders').select('*').eq('market_id', market_id).eq('status', 'open').execute()
            orders = orders_resp.data if orders_resp.data else []
            print(f"DEBUG: Found {len(orders)} orders")
            print(f"DEBUG: Orders: {orders}")
        except Exception as e:
            print(f"DEBUG: Orders query failed: {e}")
            return jsonify({'error': f'Orders query failed: {str(e)}'}), 500
        
        # Organize orders by token and side
        yes_bids = []
        yes_asks = []
        no_bids = []
        no_asks = []
        
        for order in orders:
            try:
                remaining_size = float(order['size']) - float(order.get('filled', 0))
                if remaining_size <= 0:
                    continue
                    
                order_info = {
                    'price': float(order['price']),
                    'size': remaining_size,
                    'user_id': order['user_id'],
                    'order_id': order['id']
                }
                
                print(f"DEBUG: Processing order: side={order['side']}, token={order['token']}, price={order['price']}")
                
                # side = token type (YES/NO), token = direction (BUY/SELL)
                if order['side'] == 'YES':  # YES token orders
                    if order['token'] == 'BUY':  # Buying YES tokens
                        yes_bids.append(order_info)
                    else:  # Selling YES tokens
                        yes_asks.append(order_info)
                else:  # NO token orders (side == 'NO')
                    if order['token'] == 'BUY':  # Buying NO tokens
                        no_bids.append(order_info)
                    else:  # Selling NO tokens
                        no_asks.append(order_info)
                        
            except Exception as e:
                print(f"DEBUG: Error processing order {order.get('id', 'unknown')}: {e}")
                continue
        
        # Sort orders (bids descending, asks ascending)
        yes_bids.sort(key=lambda x: x['price'], reverse=True)
        yes_asks.sort(key=lambda x: x['price'])
        no_bids.sort(key=lambda x: x['price'], reverse=True)
        no_asks.sort(key=lambda x: x['price'])
        
        result = {
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
            },
            'debug_info': {
                'total_orders': len(orders),
                'yes_bids_count': len(yes_bids),
                'yes_asks_count': len(yes_asks),
                'no_bids_count': len(no_bids),
                'no_asks_count': len(no_asks)
            }
        }
        
        print(f"DEBUG: Final result: {result}")
        return jsonify(result)
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Orderbook failed: {str(e)}'}), 500
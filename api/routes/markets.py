from flask import Blueprint, request, jsonify, render_template, g, current_app
from api.auth import login_required, admin_required
from api.utils import bootstrap_market, get_or_create_orderbook
import uuid
from datetime import datetime, timedelta, timezone

markets_bp = Blueprint('markets', __name__)

def get_current_user_id():
    """Helper function to extract user ID from g.current_user"""
    current_user = g.current_user
    if hasattr(current_user, 'id'):
        return current_user.id
    elif isinstance(current_user, dict):
        return current_user['id']
    else:
        return str(current_user)

@markets_bp.route('/create-market')
@login_required
def create_market_page():
    return render_template('create_market.html')

@markets_bp.route('/markets')
@login_required
def markets_page():
    supabase = current_app.supabase
    try:
        markets_resp = supabase.table('markets').select('*').eq('status', 'active').execute()
        available_markets = markets_resp.data if markets_resp.data else []
        return render_template('markets.html', markets=available_markets)
    except Exception as e:
        return render_template('markets.html', markets=[], error=str(e))

@markets_bp.route('/api/markets', methods=['GET'])
def get_markets():
    supabase = current_app.supabase
    try:
        markets_resp = supabase.table('markets').select('*').eq('status', 'active').execute()
        return jsonify({'success': True, 'markets': markets_resp.data if markets_resp.data else []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@markets_bp.route('/api/markets', methods=['POST'])
@admin_required
@login_required
def create_market():
    """Create a new prediction market"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['title', 'description', 'end_date']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Parse end date
        try:
            end_date = datetime.fromisoformat(data['end_date'].replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': 'Invalid end_date format. Use ISO format.'}), 400
        
        # Validate end date is in the future
        if end_date <= datetime.now(timezone.utc):
            return jsonify({'error': 'End date must be in the future'}), 400
        
        # Generate market ID
        market_id = str(uuid.uuid4())
        
        # Prepare market data
        market_data = {
            'id': market_id,
            'title': data['title'],
            'description': data['description'],
            'end_date': end_date.isoformat(),
            'status': 'active',
            'category': data.get('category', 'football'),
            'yes_price': data.get('initial_probability', 0.5),
            'no_price': 1.0 - data.get('initial_probability', 0.5),
            'total_volume': 0,
            'token': data.get('token', 'MARKET'),
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        supabase = current_app.supabase
        
        # Insert market into database
        market_resp = supabase.table('markets').insert(market_data).execute()
        
        if not market_resp.data:
            return jsonify({'error': 'Failed to create market'}), 500
        
        # Bootstrap the market with initial liquidity
        initial_prob = data.get('initial_probability', 0.5)
        if not bootstrap_market(market_id, initial_prob):
            # If bootstrap fails, still return success but log warning
            print(f"Warning: Failed to bootstrap market {market_id}")
        
        return jsonify({
            'success': True, 
            'message': 'Market created successfully',
            'market': market_resp.data[0]
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to create market: {str(e)}'}), 500

@markets_bp.route('/markets/<market_id>')
@login_required
def market_detail(market_id):
    supabase = current_app.supabase
    try:
        market_resp = supabase.table('markets').select('*').eq('id', market_id).single().execute()
        if not market_resp.data:
            return "Market not found", 404
        
        market = market_resp.data
        
        # Get current market prices from database (best bid/ask)
        market_prices = {
            'yes_price': float(market.get('yes_price', 0.5)), 
            'no_price': float(market.get('no_price', 0.5))
        }
        
        try:
            # Get best YES bid (highest buy price)
            yes_bid_resp = supabase.table('orders').select('price').eq('market_id', market_id).eq('token', 'YES').eq('side', 'buy').eq('status', 'open').order('price', desc=True).limit(1).execute()
            if yes_bid_resp.data:
                market_prices['yes_bid'] = float(yes_bid_resp.data[0]['price'])
            
            # Get best YES ask (lowest sell price)
            yes_ask_resp = supabase.table('orders').select('price').eq('market_id', market_id).eq('token', 'YES').eq('side', 'sell').eq('status', 'open').order('price', desc=False).limit(1).execute()
            if yes_ask_resp.data:
                market_prices['yes_ask'] = float(yes_ask_resp.data[0]['price'])
            
            # Get best NO bid and ask
            no_bid_resp = supabase.table('orders').select('price').eq('market_id', market_id).eq('token', 'NO').eq('side', 'buy').eq('status', 'open').order('price', desc=True).limit(1).execute()
            if no_bid_resp.data:
                market_prices['no_bid'] = float(no_bid_resp.data[0]['price'])
            
            no_ask_resp = supabase.table('orders').select('price').eq('market_id', market_id).eq('token', 'NO').eq('side', 'sell').eq('status', 'open').order('price', desc=False).limit(1).execute()
            if no_ask_resp.data:
                market_prices['no_ask'] = float(no_ask_resp.data[0]['price'])
        except Exception as e:
            print(f"Error getting market prices: {e}")
        
        return render_template('market_detail.html', 
                             market=market, 
                             market_prices=market_prices,
                             current_user=getattr(g, 'current_user', None))
    except Exception as e:
        import traceback; traceback.print_exc()
        return f"Error loading market: {e}", 500

@markets_bp.route('/api/markets/<market_id>/resolve', methods=['POST'])
@admin_required
@login_required
def resolve_market(market_id):
    """Resolve a market with final outcome"""
    try:
        data = request.get_json()
        outcome = data.get('outcome')  # True or False
        
        if outcome not in [True, False]:
            return jsonify({'error': 'Outcome must be true or false'}), 400
        
        supabase = current_app.supabase
        
        # Check if market exists and is active
        market_resp = supabase.table('markets').select('*').eq('id', market_id).single().execute()
        if not market_resp.data:
            return jsonify({'error': 'Market not found'}), 404
        
        market = market_resp.data
        if market['status'] != 'active':
            return jsonify({'error': 'Market is not active'}), 400
        
        # Update market status and outcome
        resolve_date = datetime.now(timezone.utc)
        update_resp = supabase.table('markets').update({
            'status': 'resolved',
            'resolution': outcome,
            'resolved_at': resolve_date.isoformat(),
            'resolve_date': resolve_date.isoformat()
        }).eq('id', market_id).execute()
        
        if not update_resp.data:
            return jsonify({'error': 'Failed to resolve market'}), 500
        
        # Cancel all open orders for this market
        cancel_resp = supabase.table('orders').update({
            'status': 'cancelled'
        }).eq('market_id', market_id).eq('status', 'open').execute()
        
        # Remove orderbook from memory
        if market_id in current_app.markets:
            del current_app.markets[market_id]
        
        # Process payouts to users based on their positions
        process_market_payouts(market_id, outcome, supabase)
        
        return jsonify({
            'success': True, 
            'message': f'Market resolved as {"YES" if outcome else "NO"}',
            'market': update_resp.data[0]
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to resolve market: {str(e)}'}), 500

@markets_bp.route('/api/markets/<market_id>/resolution-preview', methods=['POST'])
@login_required
def preview_resolution(market_id):
    """Preview what would happen if market was resolved with given outcome"""
    try:
        data = request.get_json()
        outcome = data.get('outcome')  # True or False
        
        if outcome not in [True, False]:
            return jsonify({'error': 'Outcome must be true or false'}), 400
        
        supabase = current_app.supabase
        
        # Get all user positions for this market
        positions_resp = supabase.table('positions').select('*').eq('market_id', market_id).execute()
        positions = positions_resp.data if positions_resp.data else []
        
        # Calculate preview payouts
        preview_data = {
            'total_payout': 0,
            'winner_count': 0,
            'loser_count': 0,
            'user_payouts': []
        }
        
        for position in positions:
            user_payout = 0
            winning_shares = 0
            losing_shares = 0
            
            if outcome:  # YES wins
                winning_shares = float(position.get('yes_shares', 0))
                losing_shares = float(position.get('no_shares', 0))
            else:  # NO wins
                winning_shares = float(position.get('no_shares', 0))
                losing_shares = float(position.get('yes_shares', 0))
            
            user_payout = winning_shares  # Each winning share pays $1
            
            if winning_shares > 0:
                preview_data['winner_count'] += 1
            if losing_shares > 0:
                preview_data['loser_count'] += 1
            
            preview_data['total_payout'] += user_payout
            preview_data['user_payouts'].append({
                'user_id': position['user_id'],
                'yes_shares': float(position.get('yes_shares', 0)),
                'no_shares': float(position.get('no_shares', 0)),
                'winning_shares': winning_shares,
                'losing_shares': losing_shares,
                'payout': user_payout
            })
        
        return jsonify({
            'success': True, 
            'preview': preview_data
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to preview resolution: {str(e)}'}), 500

@markets_bp.route('/api/markets/<market_id>/can-resolve', methods=['GET'])
@login_required
def can_resolve_market(market_id):
    """Check if current user can resolve this market"""
    try:
        supabase = current_app.supabase
        
        # Get market details
        market_resp = supabase.table('markets').select('*').eq('id', market_id).single().execute()
        if not market_resp.data:
            return jsonify({'error': 'Market not found'}), 404
        
        market = market_resp.data
        current_user = g.current_user
        
        # Extract user info safely
        user_id = get_current_user_id()
        is_admin = False
        if hasattr(current_user, 'is_admin'):
            is_admin = current_user.is_admin
        elif isinstance(current_user, dict):
            is_admin = current_user.get('is_admin', False)
        
        # Check various conditions
        can_resolve = False
        reason = ""
        
        if market['status'] != 'active':
            reason = "Market is not active"
        elif current_user.get('is_admin', False):
            # Admins can always resolve
            can_resolve = True
        else:
            # Only admins can resolve in this system
            reason = "Only admins can resolve markets"
        
        # Check if market has ended
        end_date = datetime.fromisoformat(market['end_date'].replace('Z', '+00:00'))
        market_ended = datetime.now(timezone.utc) >= end_date
        
        return jsonify({
            'can_resolve': can_resolve,
            'reason': reason,
            'market_status': market['status'],
            'market_ended': market_ended,
            'is_admin': current_user.get('is_admin', False)
        })
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Failed to check resolution permissions: {str(e)}'}), 500

@markets_bp.route('/api/markets/<market_id>/orderbook', methods=['GET'])
@login_required
def get_orderbook(market_id):
    """Get current orderbook state for a market"""
    try:
        supabase = current_app.supabase
        
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
        
        # Sort orders (bids descending by price, asks ascending by price)
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

def process_market_payouts(market_id, outcome, supabase):
    """Process payouts to users when market resolves"""
    try:
        # Get all positions for this market
        positions_resp = supabase.table('positions').select('*').eq('market_id', market_id).execute()
        positions = positions_resp.data if positions_resp.data else []
        
        for position in positions:
            user_id = position['user_id']
            
            # Calculate payout based on outcome
            if outcome:  # YES wins
                payout = float(position.get('yes_shares', 0))
            else:  # NO wins
                payout = float(position.get('no_shares', 0))
            
            if payout > 0:
                # Add payout to user balance
                user_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
                if user_resp.data:
                    current_balance = float(user_resp.data['balance'])
                    new_balance = current_balance + payout
                    
                    supabase.table('users').update({
                        'balance': new_balance
                    }).eq('id', user_id).execute()
                    
                    # Record transaction
                    supabase.table('transactions').insert({
                        'user_id': user_id,
                        'amount': payout,
                        'type': 'market_payout',
                        'description': f'Market resolution payout',
                        'market_id': market_id,
                        'created_at': datetime.now(timezone.utc).isoformat()
                    }).execute()
        
        print(f"Processed payouts for market {market_id}")
        
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"Error processing payouts: {e}")
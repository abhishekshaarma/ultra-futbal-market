from flask import Blueprint, request, jsonify, render_template, g, current_app, redirect, url_for, make_response, flash
from api.auth import login_required, get_current_user_id
from datetime import datetime, timezone

def get_user_dict(user_obj):
    if user_obj is None:
        return None
    try:
        return user_obj.model_dump()  # for pydantic models
    except Exception:
        try:
            return user_obj.__dict__

        except Exception:
            return str(user_obj)

user_bp = Blueprint('user', __name__)

@user_bp.route('/')
def home():
    # Check if user is already logged in
    token = request.cookies.get('access_token') or request.headers.get('Authorization')
    if token and token.startswith('Bearer '):
        token = token[7:]
    
    if token:
        try:
            user = current_app.supabase.auth.get_user(token)
            if user and getattr(user, 'user', None):
                # User is logged in, redirect to profile
                return redirect(url_for('user.profile'))
        except Exception:
            pass
    
    # User is not logged in, redirect to login
    return redirect(url_for('user.login'))

@user_bp.route('/about')
@login_required
def about():
    return render_template('about.html')

@user_bp.route('/debug')
@login_required
def debug():
    return render_template('debug.html')

@user_bp.route('/profile')
@login_required
def profile():
    user = getattr(g, 'current_user', None)
    user_id = getattr(user, 'id', None)
    user_email = getattr(user, 'email', None)
    username = user_email.split('@')[0] if user_email else f'user_{str(user_id)[:8]}'
    supabase = current_app.supabase

    user_profile = None
    balance = None
    positions = []
    notifications = []
    transactions = []
    errors = []

    if user_id:
        try:
            profile_resp = supabase.table('users').select('*').eq('id', user_id).single().execute()
            if not getattr(profile_resp, 'data', None):
                # Try to create the user row if missing
                supabase.table('users').insert({
                    'id': user_id,  # Changed back to 'id'
                    'username': username,
                    'display_name': username,
                }).execute()
                profile_resp = supabase.table('users').select('*').eq('id', user_id).single().execute()
            if getattr(profile_resp, 'data', None):
                user_profile = profile_resp.data
            else:
                user_profile = {'id': user_id, 'username': username, 'email': user_email}
        except Exception as e:
            user_profile = {'id': user_id, 'username': username, 'email': user_email}
        try:
            balance_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
            if balance_resp.data and 'balance' in balance_resp.data:
                balance = balance_resp.data['balance']
            else:
                balance = 1000.00
        except Exception as e:
            balance = 1000.00
        try:
            pos_resp = supabase.table('positions').select('*').eq('user_id', user_id).limit(5).execute()
            positions = pos_resp.data if hasattr(pos_resp, 'data') and pos_resp.data else []
        except Exception as e:
            errors.append(f'Error fetching positions: {e}')
        try:
            notif_resp = supabase.table('notifications').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(5).execute()
            notifications = notif_resp.data if hasattr(notif_resp, 'data') and notif_resp.data else []
        except Exception as e:
            errors.append(f'Error fetching notifications: {e}')
        try:
            tx_resp = supabase.table('transactions').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(5).execute()
            transactions = tx_resp.data if hasattr(tx_resp, 'data') and tx_resp.data else []
        except Exception as e:
            errors.append(f'Error fetching transactions: {e}')
    else:
        errors.append('No user ID found.')

    return render_template(
        'profile.html',
        user=user_profile,
        balance=balance,
        positions=positions,
        notifications=notifications,
        transactions=transactions,
        errors=errors
    )

@user_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        # Check if user is already logged in
        token = request.cookies.get('access_token') or request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        
        if token:
            try:
                user = current_app.supabase.auth.get_user(token)
                if user and getattr(user, 'user', None):
                    # User is already logged in, redirect to profile
                    return redirect(url_for('user.profile'))
            except Exception:
                pass
        
        return render_template('signup.html')
    
    # Handle both form and JSON requests
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form
    
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    # Validation
    if not email or not password:
        error_msg = 'Email and password are required.'
        if request.is_json:
            return jsonify({'error': error_msg}), 400
        return render_template('signup.html', error=error_msg)
    
    if len(password) < 6:
        error_msg = 'Password must be at least 6 characters long.'
        if request.is_json:
            return jsonify({'error': error_msg}), 400
        return render_template('signup.html', error=error_msg)
    
    try:
        # Attempt to sign up the user
        user_response = current_app.supabase.auth.sign_up({
            "email": email, 
            "password": password
        })
        
        user_data = get_user_dict(getattr(user_response, 'user', None))
        user_id = user_data.get('id') if isinstance(user_data, dict) else None
        
        if user_id:
            # Create user profile in database immediately
            username = email.split('@')[0] if email else f'user_{user_id[:8]}'
            try:
                # Use upsert to avoid conflicts if user already exists
                # Use admin client to bypass RLS policies
                admin_client = getattr(current_app, 'supabase_admin', current_app.supabase)
                
                admin_client.table('users').upsert({
                    'id': user_id,  # Changed back to 'id'
                    'username': username,
                    'display_name': username,
                    'balance': 1000.00,  # Starting balance
                    'total_volume': 0.0,
                    'created_at': datetime.now(timezone.utc).isoformat()
                }).execute()
                print(f"Successfully created user profile for {user_id}")
            except Exception as db_error:
                # Log the error but don't fail the signup
                print(f"Error creating user profile: {db_error}")
                # Try again with just the essential fields
                try:
                    admin_client = getattr(current_app, 'supabase_admin', current_app.supabase)
                    admin_client.table('users').upsert({
                        'id': user_id,  # Changed back to 'id'
                        'username': username,
                        'balance': 1000.00
                    }).execute()
                    print(f"Created minimal user profile for {user_id}")
                except Exception as retry_error:
                    print(f"Failed to create even minimal user profile: {retry_error}")
        
        success_msg = 'Signup successful! Please check your email to confirm your account.'
        if request.is_json:
            return jsonify({
                'message': success_msg, 
                'user': user_data
            }), 201
        else:
            # For form submissions, redirect to login with success message
            return redirect(url_for('user.login', message=success_msg))
            
    except Exception as e:
        error_msg = str(e)
        # Clean up common error messages
        if 'already registered' in error_msg.lower():
            error_msg = 'An account with this email already exists.'
        elif 'invalid email' in error_msg.lower():
            error_msg = 'Please enter a valid email address.'
        
        if request.is_json:
            return jsonify({'error': error_msg}), 400
        return render_template('signup.html', error=error_msg)

@user_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        # Check if user is already logged in
        token = request.cookies.get('access_token') or request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        
        if token:
            try:
                user = current_app.supabase.auth.get_user(token)
                if user and getattr(user, 'user', None):
                    # User is already logged in, redirect to profile
                    return redirect(url_for('user.profile'))
            except Exception:
                pass
        
        message = request.args.get('message')
        return render_template('login.html', message=message)
    
    # Handle both form and JSON requests
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form
    
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    # Validation
    if not email or not password:
        error_msg = 'Email and password are required.'
        if request.is_json:
            return jsonify({'error': error_msg}), 400
        return render_template('login.html', error=error_msg)
    
    try:
        # Attempt to sign in
        session_response = current_app.supabase.auth.sign_in_with_password({
            "email": email, 
            "password": password
        })
        
        session_data = getattr(session_response, 'session', None)
        user_data = get_user_dict(getattr(session_response, 'user', None))
        access_token = getattr(session_data, 'access_token', None) if session_data else None
        
        if not access_token:
            error_msg = 'Login failed. Please check your credentials.'
            if request.is_json:
                return jsonify({'error': error_msg}), 401
            return render_template('login.html', error=error_msg)
        
        # Create response
        if request.is_json:
            resp = jsonify({
                'message': 'Login successful',
                'user': user_data,
                'redirect': url_for('user.profile')
            })
        else:
            resp = redirect(url_for('user.profile'))
        
        # Set secure cookie with token
        resp = make_response(resp)
        resp.set_cookie(
            'access_token', 
            access_token, 
            httponly=True, 
            secure=False,  # Set to True in production with HTTPS
            samesite='Lax',
            max_age=3600  # 1 hour
        )
        
        return resp
        
    except Exception as e:
        error_msg = str(e)
        # Clean up common error messages
        if 'invalid login credentials' in error_msg.lower():
            error_msg = 'Invalid email or password.'
        elif 'email not confirmed' in error_msg.lower():
            error_msg = 'Please check your email and confirm your account before logging in.'
        
        if request.is_json:
            return jsonify({'error': error_msg}), 401
        return render_template('login.html', error=error_msg)

@user_bp.route('/logout')
def logout():
    resp = make_response(redirect(url_for('user.login')))
    resp.set_cookie('access_token', '', expires=0, httponly=True)
    return resp 

 
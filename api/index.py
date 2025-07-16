from turtle import Turtle
from flask import Flask, render_template_string
import os
from supabase import create_client, Client
from dotenv import load_dotenv
from flask import request, jsonify, render_template, session as flask_session, redirect, url_for, g, make_response
from functools import wraps

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_API_KEY')
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError('SUPABASE_URL and SUPABASE_KEY must be set in environment variables')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)



@app.route('/')
def home():
    return render_template('index.html')


@app.route('/about')
def about():
    return 'About'

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

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.cookies.get('access_token') or request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        if not token:
            return redirect(url_for('login'))
        try:
            user = supabase.auth.get_user(token)
            user_obj = getattr(user, 'user', None)
            if user_obj is None:
                return redirect(url_for('login'))
            g.current_user = user_obj
        except Exception:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        return render_template('signup.html')
    data = request.get_json() if request.is_json else request.form
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({'error': 'Email and password are required.'}), 400
    try:
        user_response = supabase.auth.sign_up({"email": email, "password": password})
        user_data = get_user_dict(getattr(user_response, 'user', None))
        # After successful signup, insert into users table if user_data is valid
        user_id = user_data.get('id') if isinstance(user_data, dict) else None
        if user_id:
            username = email.split('@')[0] if email else f'user_{user_id[:8]}'
            supabase.table('users').insert({
                'id': user_id,
                'username': username,
                'display_name': username,
                # ... any other defaults
            }).execute()
        return jsonify({'message': 'Signup successful. Please check your email to confirm your account.', 'user': user_data}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    data = request.get_json() if request.is_json else request.form
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return render_template('login.html', error='Email and password are required.')
    try:
        session_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        session_data = getattr(session_response, 'session', None)
        user_data = get_user_dict(getattr(session_response, 'user', None))
        access_token = getattr(session_data, 'access_token', None) if session_data else None
        resp = redirect(url_for('profile'))
        if access_token:
            resp = make_response(resp)
            resp.set_cookie('access_token', access_token, httponly=True)
        return resp
    except Exception as e:
        return render_template('login.html', error=str(e))

@app.route('/logout')
def logout():
    resp = make_response(redirect(url_for('login')))
    resp.set_cookie('access_token', '', expires=0)
    return resp

@app.route('/profile')
@login_required
def profile():
    user = getattr(g, 'current_user', None)
    user_id = getattr(user, 'id', None)
    user_email = getattr(user, 'email', None)
    username = user_email.split('@')[0] if user_email else f'user_{str(user_id)[:8]}'  # Always define username
    # Fetch user profile
    user_profile = None
    balance = None
    positions = []
    notifications = []
    transactions = []
    errors = []
    if user_id:
        # User profile
        try:
            profile_resp = supabase.table('users').select('*').eq('id', user_id).single().execute()
            if not getattr(profile_resp, 'data', None):
                # Try to create the user row if missing
                insert_resp = supabase.table('users').insert({
                    'id': user_id,
                    'username': username,
                    'display_name': username,
                }).execute()
                # Try fetching again
                profile_resp = supabase.table('users').select('*').eq('id', user_id).single().execute()
            if getattr(profile_resp, 'data', None):
                user_profile = profile_resp.data
            else:
                user_profile = {'id': user_id, 'username': username, 'email': user_email}
                # Do not append any error or warning here for cleaner UI
        except Exception as e:
            user_profile = {'id': user_id, 'username': username, 'email': user_email}
            # Do not append error for fallback minimal info
        # Balance
        try:
            balance_resp = supabase.table('users').select('balance').eq('id', user_id).single().execute()
            if balance_resp.data and 'balance' in balance_resp.data:
                balance = balance_resp.data['balance']
            else:
                balance = 1000.00  # Default starting balance
                # Do not append any error or warning here for cleaner UI
        except Exception as e:
            balance = 100.00
            # Do not append error for fallback default balance
        # Positions
        try:
            pos_resp = supabase.table('positions').select('*').eq('user_id', user_id).limit(5).execute()
            positions = pos_resp.data if hasattr(pos_resp, 'data') and pos_resp.data else []
        except Exception as e:
            errors.append(f'Error fetching positions: {e}')
        # Notifications
        try:
            notif_resp = supabase.table('notifications').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(5).execute()
            notifications = notif_resp.data if hasattr(notif_resp, 'data') and notif_resp.data else []
        except Exception as e:
            errors.append(f'Error fetching notifications: {e}')
        # Transactions
        try:
            tx_resp = supabase.table('transactions').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(5).execute()
            transactions = tx_resp.data if hasattr(tx_resp, 'data') and tx_resp.data else []
        except Exception as e:
            errors.append(f'Error fetching transactions: {e}')
    else:
        errors.append('No user ID found.')
    return render_template('profile.html', user=user_profile, balance=balance, positions=positions, notifications=notifications, transactions=transactions, errors=errors)

if __name__ == "__main__":
    app.run(port=5000, debug=True)
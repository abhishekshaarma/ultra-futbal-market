from flask import Blueprint, request, jsonify, render_template, g, current_app, redirect, url_for, make_response
from api.auth import login_required

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
                    'id': user_id,
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
        return render_template('signup.html')
    data = request.get_json() if request.is_json else request.form
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({'error': 'Email and password are required.'}), 400
    try:
        user_response = current_app.supabase.auth.sign_up({"email": email, "password": password})
        user_data = get_user_dict(getattr(user_response, 'user', None))
        user_id = user_data.get('id') if isinstance(user_data, dict) else None
        if user_id:
            username = email.split('@')[0] if email else f'user_{user_id[:8]}'
            current_app.supabase.table('users').insert({
                'id': user_id,
                'username': username,
                'display_name': username,
            }).execute()
        return jsonify({'message': 'Signup successful. Please check your email to confirm your account.', 'user': user_data}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@user_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    data = request.get_json() if request.is_json else request.form
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return render_template('login.html', error='Email and password are required.')
    try:
        session_response = current_app.supabase.auth.sign_in_with_password({"email": email, "password": password})
        session_data = getattr(session_response, 'session', None)
        user_data = get_user_dict(getattr(session_response, 'user', None))
        access_token = getattr(session_data, 'access_token', None) if session_data else None
        resp = redirect(url_for('user.profile'))
        if access_token:
            resp = make_response(resp)
            resp.set_cookie('access_token', access_token, httponly=True)
        return resp
    except Exception as e:
        return render_template('login.html', error=str(e))

@user_bp.route('/logout')
def logout():
    resp = make_response(redirect(url_for('user.login')))
    resp.set_cookie('access_token', '', expires=0)
    return resp 
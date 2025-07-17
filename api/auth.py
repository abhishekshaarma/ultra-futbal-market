from flask import Blueprint, request, redirect, url_for, g, jsonify, session as flask_session
from functools import wraps
from flask import current_app as app

auth_bp = Blueprint('auth', __name__)

def is_admin(user_id):
    try:
        user_resp = app.supabase.table('users').select('is_admin').eq('id', user_id).single().execute()
        return user_resp.data and user_resp.data.get('is_admin', False)
    except Exception:
        return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.cookies.get('access_token') or request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        if not token:
            return redirect(url_for('user.login'))
        try:
            user = app.supabase.auth.get_user(token)
            user_obj = getattr(user, 'user', None)
            if user_obj is None:
                return redirect(url_for('user.login'))
            g.current_user = user_obj
        except Exception:
            return redirect(url_for('user.login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not hasattr(g, 'current_user') or not is_admin(g.current_user.id):
            return jsonify({'error': 'Admin privileges required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# Add auth routes (login, logout, signup) here as needed 
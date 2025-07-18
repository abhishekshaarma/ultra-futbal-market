from flask import Blueprint, request, redirect, url_for, g, jsonify, session as flask_session
from functools import wraps
from flask import current_app as app

auth_bp = Blueprint('auth', __name__)

def is_admin(user_id):
    """Check if user is admin by looking up in users table"""
    try:
        user_resp = app.supabase.table('users').select('is_admin').eq('id', user_id).single().execute()
        return user_resp.data and user_resp.data.get('is_admin', False)
    except Exception:
        return False

def get_current_user_id():
    """Helper function to extract user ID from g.current_user"""
    current_user = g.current_user
    if hasattr(current_user, 'id'):
        return current_user.id
    elif isinstance(current_user, dict):
        return current_user['id']
    else:
        return str(current_user)

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
        if not hasattr(g, 'current_user'):
            return jsonify({'error': 'Authentication required'}), 401
        
        user_id = get_current_user_id()
        if not is_admin(user_id):
            return jsonify({'error': 'Admin privileges required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# Add auth routes (login, logout, signup) here as needed 
import os
from flask import Flask
from dotenv import load_dotenv
from supabase import create_client, Client


def create_app():
    load_dotenv()
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
        template_folder=os.path.join(os.path.dirname(__file__), "templates")
    )
    app.config.from_object('api.config.Config')
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key")

    # Supabase client
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_API_KEY')
    SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')  # Service role key for admin operations
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError('SUPABASE_URL and SUPABASE_KEY must be set in environment variables')
    
    setattr(app, "supabase", create_client(SUPABASE_URL, SUPABASE_KEY))
    
    # Service role client for admin operations (bypasses RLS)
    if SUPABASE_SERVICE_KEY:
        setattr(app, "supabase_admin", create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY))
    else:
        # Fallback to regular client if service key not available
        setattr(app, "supabase_admin", create_client(SUPABASE_URL, SUPABASE_KEY))

    # Orderbook markets dict (in-memory)
    setattr(app, "markets", {})

    # Register blueprints
    from api.routes.main import main_bp
    from api.routes.markets import markets_bp
    from api.routes.trading import trading_bp
    from api.routes.user import user_bp
    from api.auth import auth_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(markets_bp)
    app.register_blueprint(trading_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(auth_bp)

    # Load all orderbooks from DB on startup
    from api.utils import load_all_orderbooks_from_db
    with app.app_context():
        load_all_orderbooks_from_db()

    return app

# At the end of the file, expose the app object for Vercel
app = create_app()




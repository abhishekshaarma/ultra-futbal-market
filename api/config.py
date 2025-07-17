import os

class Config:
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'super-secret-key')
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY')
    DEBUG = os.getenv('FLASK_DEBUG', '0') == '1'
    # Add other config options as needed 
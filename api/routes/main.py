
from flask import Blueprint, render_template

main_bp = Blueprint('main_routes', __name__)

# Remove the '/' and '/about' routes, as they are now in user.py 
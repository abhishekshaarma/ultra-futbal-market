from api import create_app
from api.routes.main import main_bp

app = create_app()
app.register_blueprint(main_bp)

if __name__ == "__main__":
    app.run(port=5000)
    
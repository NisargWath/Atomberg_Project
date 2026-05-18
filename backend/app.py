from flask import Flask, g
from flask_cors import CORS

from config.settings import settings
from models.store import get_db
from routes.admin import admin_bp
from routes.ai import ai_bp
from routes.auth import auth_bp
from routes.goals import goals_bp
from routes.manager import manager_bp
from utils.responses import ok, respond

db = get_db()


def create_app():
    app = Flask(__name__)
    CORS(app, origins=[
        settings.FRONTEND_ORIGIN,
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
    ])

    @app.before_request
    def attach_db():
        g.db = db

    @app.get("/health")
    def health():
        return respond(*ok({"storage": "sqlite", "db_path": settings.SQLITE_DB_PATH}, "Goal portal backend is running"))

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(goals_bp, url_prefix="/api/goals")
    app.register_blueprint(manager_bp, url_prefix="/api/manager")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(ai_bp, url_prefix="/api/ai")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.PORT, debug=True)

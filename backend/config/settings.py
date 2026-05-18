import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
    JWT_ALGORITHM = "HS256"
    SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "goal_portal.db")
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    PORT = int(os.getenv("PORT", "5001"))
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    GEMINI_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "8"))
    DEMO_MODE = False


settings = Settings()

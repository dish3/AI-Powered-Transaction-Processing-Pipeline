import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database configuration (default host updated from db to postgres)
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres:5432/transactions_db"

    # Redis configuration (used by Celery)
    REDIS_URL: str = "redis://redis:6379/0"

    # Gemini LLM configuration
    GEMINI_API_KEY: str = ""

    # File uploads directory
    UPLOAD_DIR: str = "uploads"

    # CORS configuration
    ALLOWED_ORIGINS: str = "*"

    # Settings configurations to load from environment variables / .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings to import across the app
settings = Settings()

# Ensure upload directory exists
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

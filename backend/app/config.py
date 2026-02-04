# app/config.py
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    MONGO_URI: str = "mongodb://localhost:27017"
    DB_NAME: str = "disclaimer_checker"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: Optional[str] = None  # Optional model name
    ENV: Optional[str] = None  # Optional environment name
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields from .env


settings = Settings()

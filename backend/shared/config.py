import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

class Config:
    DATABASE_URL: str = os.getenv('DATABASE_URL', '')
    REDIS_URL: str = os.getenv('REDIS_URL', 'redis://localhost:6379')
    NEWSAPI_KEY: str = os.getenv('NEWSAPI_KEY', '')
    NYT_API_KEY: str = os.getenv('NYT_API_KEY', '')
    TELEGRAM_API_ID: int = int(os.getenv('TELEGRAM_API_ID', '0'))
    TELEGRAM_API_HASH: str = os.getenv('TELEGRAM_API_HASH', '')
    COSINE_SIMILARITY_THRESHOLD: float = float(os.getenv('COSINE_SIMILARITY_THRESHOLD', '0.65'))
    NLI_CONFIDENCE_THRESHOLD: float = float(os.getenv('NLI_CONFIDENCE_THRESHOLD', '0.70'))
    APP_ENV: str = os.getenv('APP_ENV', 'development')

    @classmethod
    def validate(cls):
        # Required for any worker run
        missing = []
        if not cls.DATABASE_URL:
            missing.append('DATABASE_URL')
        if not cls.NEWSAPI_KEY:
            missing.append('NEWSAPI_KEY')
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        # Soft warnings for optional features
        warnings = []
        if not cls.TELEGRAM_API_ID or not cls.TELEGRAM_API_HASH:
            warnings.append('TELEGRAM_API_* not set — Telegram sources disabled')
        if warnings:
            for w in warnings:
                print(f"[config] {w}")

config = Config()
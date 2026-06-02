import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

class Config:
    DATABASE_URL: str = os.getenv('DATABASE_URL', '')
    # Redis removed — dedup now uses an in-memory URL hash set (see deduplication.py).
    COSINE_SIMILARITY_THRESHOLD: float = float(os.getenv('COSINE_SIMILARITY_THRESHOLD', '0.65'))
    NLI_CONFIDENCE_THRESHOLD: float = float(os.getenv('NLI_CONFIDENCE_THRESHOLD', '0.70'))
    APP_ENV: str = os.getenv('APP_ENV', 'development')

    @classmethod
    def validate(cls):
        """Fail fast on missing critical config. Called at scheduler startup."""
        if not cls.DATABASE_URL:
            raise ValueError("Missing required environment variable: DATABASE_URL")

config = Config()
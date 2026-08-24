import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

class Config:
    DATABASE_URL: str = os.getenv('DATABASE_URL', '')
    JINA_API_KEY: str = os.getenv('JINA_API_KEY', '')
    GROQ_API_KEY: str = os.getenv('GROQ_API_KEY', '')
    # Redis removed — dedup now uses an in-memory URL hash set (see deduplication.py).
    COSINE_SIMILARITY_THRESHOLD: float = float(os.getenv('COSINE_SIMILARITY_THRESHOLD', '0.65'))
    NLI_CONFIDENCE_THRESHOLD: float = float(os.getenv('NLI_CONFIDENCE_THRESHOLD', '0.70'))
    APP_ENV: str = os.getenv('APP_ENV', 'development')

    @classmethod
    def validate(cls):
        """Fail fast on missing critical config. Called at scheduler startup."""
        if not cls.DATABASE_URL:
            raise ValueError("Missing required environment variable: DATABASE_URL")
        # Non-fatal warnings — app can run but key pipelines will silently no-op without these
        if not cls.JINA_API_KEY:
            print("WARNING: JINA_API_KEY not set — Task 9 embeddings will be skipped every cycle")
        if not cls.GROQ_API_KEY:
            print("WARNING: GROQ_API_KEY not set — NLI and summarisation tasks will be skipped every cycle")

config = Config()
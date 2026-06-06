import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from backend.shared.config import config


@contextmanager
def get_db_connection():
    """
    Synchronous PostgreSQL connection context manager.
    Used by the ingestion worker for writes.
    Usage:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    # psycopg2 < 2.9 doesn't recognise the postgres:// scheme (used by Supabase
    # pooler URLs). Normalise to postgresql:// so it always parses correctly.
    _url = config.DATABASE_URL
    if _url.startswith("postgres://"):
        _url = "postgresql://" + _url[len("postgres://"):]
    conn = psycopg2.connect(_url, connect_timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_source_map() -> dict:
    """
    Returns a dict mapping source_code -> (source_id, trust_weight)
    for all active sources. Called once at worker startup.
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT code, source_id, trust_weight
                FROM sources
                WHERE is_active = TRUE
            """)
            rows = cur.fetchall()
    return {row['code']: (row['source_id'], float(row['trust_weight'])) for row in rows}
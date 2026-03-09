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
    conn = psycopg2.connect(config.DATABASE_URL)
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
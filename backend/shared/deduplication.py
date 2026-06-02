"""URL deduplication — in-memory set backed by the DB.

Strategy:
  On first use, load all known article URL hashes from the DB into a Python
  set. Each check is O(1) with no network call. When a new article is
  inserted the caller marks it here so the set stays current for the rest
  of the cycle. On worker restart the set is rebuilt from the DB.

  This replaces the Redis bitmap approach. Redis on Render's internal
  network is unreachable (connection refused), and an in-process set is
  actually faster (no network round-trip) and costs nothing.

  Memory: each MD5 hex string is 32 bytes. At 200k articles that's ~6MB.
  Render free tier has 512MB — no issue.
"""
import hashlib
import logging
from typing import Optional

log = logging.getLogger(__name__)

# The set of MD5 hashes of all known article URLs.
# None = not yet initialised (load on first use).
_seen: Optional[set] = None


def _hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _load_from_db() -> set:
    """Pull all existing article URLs from the DB and return as a hash set."""
    from backend.shared.database import get_db_connection
    hashes = set()
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT url FROM articles WHERE url IS NOT NULL")
                for (url,) in cur.fetchall():
                    hashes.add(_hash(url))
        log.info(f"[dedup] Loaded {len(hashes):,} known URLs into memory")
    except Exception as e:
        log.warning(f"[dedup] Could not pre-load URL cache from DB: {e}")
    return hashes


def _get_seen() -> set:
    """Lazy-init the seen-URL set."""
    global _seen
    if _seen is None:
        _seen = _load_from_db()
    return _seen


def check_and_mark(url: str) -> bool:
    """
    Returns True if this URL has been seen before (duplicate).
    Returns False if it's new, and marks it as seen.

    The old signature accepted an optional Redis client as the first argument.
    That argument is now ignored so existing call sites don't need to change.
    """
    seen = _get_seen()
    h = _hash(url)
    if h in seen:
        return True
    seen.add(h)
    return False


def reset():
    """Force a full reload from DB on the next check. Call if you suspect
    the in-memory set is stale (e.g. after a long pause or manual DB edit)."""
    global _seen
    _seen = None


# ---------------------------------------------------------------------------
# Backward-compat shim — worker.py calls get_redis_client() and passes the
# result as the first arg to check_and_mark(). Keep these so the worker
# doesn't need to change yet.
# ---------------------------------------------------------------------------
def get_redis_client():
    """Deprecated. Returns None. Redis is no longer used for dedup."""
    return None

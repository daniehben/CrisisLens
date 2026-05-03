import hashlib
import logging
import redis
from typing import Optional
from backend.shared.config import Config

log = logging.getLogger(__name__)

BITMAP_KEY = "crisislens:seen_urls"
TTL_SECONDS = 48 * 60 * 60  # 48 hours

# Module-level flag: once Redis fails, stop trying for this process lifetime.
# Avoids paying a connect-timeout cost on every single article.
_redis_disabled = False


def _url_to_bit(url: str) -> int:
    """Convert URL to a bit index in the bitmap (~4M bits = 512KB)."""
    digest = hashlib.md5(url.encode()).hexdigest()
    return int(digest[:8], 16) % (8 * 1024 * 512)


def get_redis_client() -> Optional[redis.Redis]:
    """
    Returns a connected Redis client, or None if Redis is unreachable.
    Probes the connection once with a short timeout so we fail fast instead
    of blocking 10s per article when the worker can't reach Redis.
    """
    global _redis_disabled
    if _redis_disabled:
        return None

    config = Config()
    try:
        client = redis.from_url(
            config.REDIS_URL,
            decode_responses=False,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        client.ping()  # actually open the socket
        return client
    except Exception as e:
        log.warning(f"[dedup] Redis unreachable, falling back to DB-only dedup: {e}")
        _redis_disabled = True
        return None


def check_and_mark(r: Optional[redis.Redis], url: str) -> bool:
    """
    Atomic check-and-mark.
    Returns True if duplicate (already seen), False if new (and marks it).
    If Redis is unavailable, returns False — DB UNIQUE constraint will catch
    real duplicates downstream in db_writer.write_article.
    """
    if r is None:
        return False

    try:
        bit = _url_to_bit(url)
        pipe = r.pipeline()
        pipe.getbit(BITMAP_KEY, bit)
        pipe.setbit(BITMAP_KEY, bit, 1)
        pipe.expire(BITMAP_KEY, TTL_SECONDS)
        results = pipe.execute()
        return bool(results[0])  # True = duplicate
    except Exception as e:
        # If Redis dies mid-cycle, disable it for the rest of this run.
        global _redis_disabled
        log.warning(f"[dedup] Redis call failed, disabling for this run: {e}")
        _redis_disabled = True
        return False
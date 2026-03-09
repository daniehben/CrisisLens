import hashlib
import redis
from backend.shared.config import Config

BITMAP_KEY = "crisislens:seen_urls"
TTL_SECONDS = 48 * 60 * 60  # 48 hours


def _url_to_bit(url: str) -> int:
    """Convert URL to a 32-bit integer for bitmap indexing."""
    digest = hashlib.md5(url.encode()).hexdigest()
    return int(digest[:8], 16) % (2 ** 32)


def get_redis_client() -> redis.Redis:
    config = Config()
    return redis.from_url(config.REDIS_URL, decode_responses=False)


def is_duplicate(r: redis.Redis, url: str) -> bool:
    """Return True if URL has been seen before."""
    bit = _url_to_bit(url)
    return bool(r.getbit(BITMAP_KEY, bit))


def mark_seen(r: redis.Redis, url: str) -> None:
    """Mark URL as seen and refresh TTL."""
    bit = _url_to_bit(url)
    r.setbit(BITMAP_KEY, bit, 1)
    r.expire(BITMAP_KEY, TTL_SECONDS)


def check_and_mark(r: redis.Redis, url: str) -> bool:
    """
    Atomic check-and-mark using a pipeline.
    Returns True if duplicate (already seen), False if new (and marks it).
    """
    bit = _url_to_bit(url)
    pipe = r.pipeline()
    pipe.getbit(BITMAP_KEY, bit)
    pipe.setbit(BITMAP_KEY, bit, 1)
    pipe.expire(BITMAP_KEY, TTL_SECONDS)
    results = pipe.execute()
    was_set = bool(results[0])
    return was_set  # True = duplicate, False = new
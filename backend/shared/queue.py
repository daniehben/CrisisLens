import json
import redis
from dataclasses import asdict
from datetime import datetime
from backend.shared.models import RawArticle

QUEUE_KEY = "crisislens:article_queue"
MAX_QUEUE_SIZE = 10_000


def enqueue_article(r: redis.Redis, article: RawArticle) -> bool:
    """
    Push article onto the queue. Returns True if enqueued, False if queue full.
    """
    if r.llen(QUEUE_KEY) >= MAX_QUEUE_SIZE:
        print(f"[queue] Queue full ({MAX_QUEUE_SIZE}), dropping article: {article.url}")
        return False

    data = asdict(article)
    # Convert datetime to ISO string for JSON serialization
    if isinstance(data.get('published_at'), datetime):
        data['published_at'] = data['published_at'].isoformat()

    r.lpush(QUEUE_KEY, json.dumps(data))
    return True


def dequeue_article(r: redis.Redis, timeout: int = 5) -> RawArticle | None:
    """
    Blocking pop from queue. Returns RawArticle or None if timeout.
    """
    result = r.brpop(QUEUE_KEY, timeout=timeout)
    if not result:
        return None

    _, raw = result
    data = json.loads(raw)

    if isinstance(data.get('published_at'), str):
        data['published_at'] = datetime.fromisoformat(data['published_at'])

    return RawArticle(**data)


def queue_size(r: redis.Redis) -> int:
    return r.llen(QUEUE_KEY)


def flush_queue(r: redis.Redis) -> None:
    """Clear the queue — used in tests."""
    r.delete(QUEUE_KEY)
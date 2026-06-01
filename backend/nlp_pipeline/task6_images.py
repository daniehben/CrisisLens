"""Task 6 — OG image backfill.

For articles that have no image_url, fetch the article page (first 16KB only,
enough to read <head>) and extract og:image or twitter:image.

Why this is safe:
  - og:image is metadata publishers deliberately set for sharing/embedding.
  - We only read page metadata, not article body.
  - Every aggregator (Google News, Flipboard, Apple News) does the same thing.

Runs once per pipeline cycle. Processes up to BATCH_SIZE articles.
Skips articles older than MAX_AGE_DAYS (stale pages may 404 or have changed).
"""
import logging
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

BATCH_SIZE = 30          # articles per cycle — keeps the cycle under ~60s
MAX_AGE_DAYS = 14        # don't bother fetching pages older than this
STREAM_READ_BYTES = 16384  # 16KB — almost always contains full <head>
REQUEST_TIMEOUT = 8      # seconds per article

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; CrisisLens/1.0; +https://crisislens.com) '
        'AppleWebKit/537.36'
    ),
    'Accept': 'text/html',
    'Accept-Language': 'en,ar;q=0.9',
}


def _fetch_og_image(url: str) -> str | None:
    """Stream first 16KB of the page, extract og:image / twitter:image."""
    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers=HEADERS,
        ) as client:
            with client.stream('GET', url) as r:
                if r.status_code >= 400:
                    return None
                content = b''
                for chunk in r.iter_bytes(chunk_size=4096):
                    content += chunk
                    # Stop once we've seen </head> or read enough
                    if b'</head>' in content.lower() or len(content) >= STREAM_READ_BYTES:
                        break

        soup = BeautifulSoup(content, 'html.parser')

        # og:image (preferred — highest resolution)
        tag = soup.find('meta', property='og:image')
        if tag and tag.get('content', '').startswith('http'):
            return tag['content']

        # twitter:image (fallback)
        tag = soup.find('meta', attrs={'name': 'twitter:image'})
        if tag and tag.get('content', '').startswith('http'):
            return tag['content']

        # twitter:image:src (some outlets use this variant)
        tag = soup.find('meta', attrs={'name': 'twitter:image:src'})
        if tag and tag.get('content', '').startswith('http'):
            return tag['content']

        return None

    except Exception:
        return None


def run_task6():
    log.info("[Task6] Starting OG image backfill...")

    cutoff = datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, url
                FROM articles
                WHERE image_url IS NULL
                  AND url IS NOT NULL
                  AND published_at >= %s
                ORDER BY published_at DESC
                LIMIT %s
            """, (cutoff, BATCH_SIZE))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task6] No articles need image backfill.")
        return 0

    log.info(f"[Task6] Fetching OG images for {len(rows)} articles...")

    filled = 0
    skipped = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, url in rows:
                img = _fetch_og_image(url)
                if img:
                    cur.execute(
                        "UPDATE articles SET image_url = %s WHERE article_id = %s",
                        (img, article_id)
                    )
                    filled += 1
                else:
                    # Write a sentinel so we don't retry this article every cycle
                    cur.execute(
                        "UPDATE articles SET image_url = '' WHERE article_id = %s",
                        (article_id,)
                    )
                    skipped += 1
        conn.commit()

    log.info(f"[Task6] Complete — {filled} images found, {skipped} not available")
    return filled

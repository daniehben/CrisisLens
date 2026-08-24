"""Task 6 — OG image backfill with topic fallback.

Strategy (in priority order):
  1. Try og:image / twitter:image from the article's own page (<head> only, 16KB cap).
  2. If the page has no og:image, write a topic-based fallback image keyed on
     the article's source code — so every article always has *something* to show.
  3. Only the old empty-string sentinel ('') is re-tried now that we have
     fallbacks — the WHERE clause includes `image_url = ''` to backfill articles
     that were written before topic fallbacks existed.

Why topic fallbacks are safe:
  - og:image is metadata publishers deliberately set for sharing/embedding.
  - Topic images are generic Unsplash CDN photos (Unsplash License — free for
    editorial and product use). They contain no person identifiers.
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

# ── Source → topic mapping ────────────────────────────────────────────────────
# Drives the fallback image when a source's article page has no og:image.
# Keys are source codes from the sources table.
SOURCE_TOPIC: dict[str, str] = {
    # Global wire / mainstream
    'AP':   'news',      'REU':  'news',      'BBC':  'news',
    'WP':   'politics',  'GUA':  'politics',  'CNN':  'news',
    # Arabic broadcasters
    'AJA':  'conflict',  'AJA+': 'conflict',  'BBAR': 'news',
    'ARB':  'politics',  'ASH':  'politics',  'SKA':  'politics',
    'DW':   'news',      'F24':  'news',       'ANA':  'politics',
    # Israel / Palestine focus
    'JRP':  'conflict',  'MEE':  'conflict',  'WAF':  'humanitarian',
    'MAN':  'humanitarian', 'HAA': 'conflict', 'EI':  'humanitarian',
    'TNA':  'conflict',
    # Iran / resistance axis
    'TAS':  'conflict',  'PTV':  'conflict',  'AKH':  'conflict',
    'CRA':  'conflict',  'MAYE': 'conflict',
    # Russia / anti-establishment
    'RTA':  'politics',  'GG':   'politics',  'GZ':   'politics',
    'CJ':   'politics',  'AW':   'politics',
    # Africa
    'SDT':  'conflict',
    # Breaking / OSINT / Telegram
    'BNO':  'news',      'WM':   'conflict',  'SI':   'conflict',
    'DSN':  'politics',  'MND':  'humanitarian',
    # YouTube commentary
    'YT_BP':'politics',  'YT_DN':'politics',  'YT_RT':'conflict',
}

# ── Topic → fallback image URL ────────────────────────────────────────────────
# Stable Unsplash CDN images (free under the Unsplash License).
# w=1200&q=80 gives social-card quality without pulling the full original.
TOPIC_FALLBACK_IMAGES: dict[str, str] = {
    'conflict':    (
        'https://images.unsplash.com/photo-1614027164847-1b28cfe1df60'
        '?w=1200&q=80&auto=format&fit=crop'
    ),
    'politics':    (
        'https://images.unsplash.com/photo-1529107386315-e1a2ed48a620'
        '?w=1200&q=80&auto=format&fit=crop'
    ),
    'humanitarian': (
        'https://images.unsplash.com/photo-1488521787991-ed7bbaae773c'
        '?w=1200&q=80&auto=format&fit=crop'
    ),
    'news':        (
        'https://images.unsplash.com/photo-1504711434969-e33886168f5c'
        '?w=1200&q=80&auto=format&fit=crop'
    ),
}
_DEFAULT_FALLBACK = TOPIC_FALLBACK_IMAGES['news']


def _topic_fallback(source_code: str | None) -> str:
    """Return the fallback image URL for a given source code."""
    topic = SOURCE_TOPIC.get(source_code or '', 'news')
    return TOPIC_FALLBACK_IMAGES.get(topic, _DEFAULT_FALLBACK)


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
            # Include image_url = '' sentinel so previously-skipped articles
            # get backfilled now that we have topic fallback images.
            cur.execute("""
                SELECT a.article_id, a.url, s.code AS source_code
                FROM articles a
                JOIN sources s ON s.source_id = a.source_id
                WHERE (a.image_url IS NULL OR a.image_url = '')
                  AND a.url IS NOT NULL
                  AND a.published_at >= %s
                ORDER BY a.published_at DESC
                LIMIT %s
            """, (cutoff, BATCH_SIZE))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task6] No articles need image backfill.")
        return 0

    log.info(f"[Task6] Fetching OG images for {len(rows)} articles...")

    og_found   = 0
    fallback   = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, url, source_code in rows:
                img = _fetch_og_image(url)
                if img:
                    cur.execute(
                        "UPDATE articles SET image_url = %s WHERE article_id = %s",
                        (img, article_id)
                    )
                    og_found += 1
                else:
                    # No og:image — write topic-based fallback so the card
                    # always has something to render. Never write bare '' again.
                    fallback_url = _topic_fallback(source_code)
                    cur.execute(
                        "UPDATE articles SET image_url = %s WHERE article_id = %s",
                        (fallback_url, article_id)
                    )
                    fallback += 1
        conn.commit()

    log.info(
        f"[Task6] Complete — {og_found} og:images found, "
        f"{fallback} topic fallbacks written"
    )
    return og_found

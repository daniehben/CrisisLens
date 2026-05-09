"""Task 7 — fetch full article bodies.

Most RSS feeds and NewsAPI return only a 50-300 char summary. To give the
modal real depth (and to feed better text to downstream NLP), we fetch each
article's URL once, extract the main body, and store ~1500 chars in
articles.body_snippet.

Heuristics:
  1. Look for <article>, <main>, or known article-content containers.
  2. Fall back to the largest cluster of <p> tags in the document.
  3. Strip nav/footer/script/aside/ads/social share blocks.

The fetch path uses a browser-like User-Agent and a short timeout. Render
Frankfurt IPs are blocked by some publishers (see CLAUDE.md) — those just
silently fail and we keep the original short snippet.
"""
import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

# Targets per cycle. Bigger batches = longer cycles + more 429 risk.
BATCH_SIZE = 30
MIN_EXISTING_BODY = 250        # only fetch if current snippet shorter than this
MAX_BODY_CHARS    = 1500       # truncation target for storage
FETCH_TIMEOUT_S   = 10
LOOKBACK_DAYS     = 7          # don't backfill ancient articles

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en;q=0.9",
}

JUNK_TAGS = ("script", "style", "noscript", "nav", "footer", "header",
             "aside", "form", "iframe", "svg", "button")
JUNK_PATTERNS = ("nav", "footer", "sidebar", "comment", "social", "share",
                 "subscribe", "newsletter", "promo", "advert", "related",
                 "recommend", "popup", "cookie", "menu", "breadcrumb")


def _is_junk(tag) -> bool:
    attrs = " ".join(filter(None, [
        " ".join(tag.get("class") or []),
        tag.get("id") or "",
        tag.get("role") or "",
    ])).lower()
    return any(p in attrs for p in JUNK_PATTERNS)


def _node_text(node) -> str:
    paragraphs = node.find_all("p")
    src = paragraphs if paragraphs else [node]
    text = " ".join(p.get_text(" ", strip=True) for p in src
                    if p.get_text(strip=True))
    return " ".join(text.split())


def _extract_body(html: str) -> Optional[str]:
    """Best-effort article body extraction. Returns None if nothing useful found."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    for t in soup(JUNK_TAGS):
        t.decompose()
    for t in soup.find_all(True):
        if _is_junk(t):
            t.decompose()

    # 1) Try semantic / known article containers
    for selector in ["article", "main", '[itemprop="articleBody"]',
                     ".article-body", ".story-body", ".article__content",
                     ".post-content", ".entry-content"]:
        node = soup.select_one(selector)
        if node:
            text = _node_text(node)
            if len(text) >= 200:
                return text

    # 2) Fall back: all <p> tags
    paragraphs = soup.find_all("p")
    if not paragraphs:
        return None
    text = " ".join(p.get_text(" ", strip=True) for p in paragraphs
                    if p.get_text(strip=True))
    text = " ".join(text.split())
    return text if len(text) >= 200 else None


def fetch_body(url: str) -> Optional[str]:
    """Fetch and extract the article body. Returns None on any failure."""
    try:
        with httpx.Client(
            headers=HEADERS,
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            return _extract_body(resp.text)
    except Exception as e:
        log.debug(f"[Task7] fetch failed for {url[:60]}: {e}")
        return None


def run_task7():
    log.info("[Task7] Starting article body fetch...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, url
                FROM articles
                WHERE url IS NOT NULL
                  AND fetched_at > NOW() - INTERVAL '%s days'
                  AND (body_snippet IS NULL OR LENGTH(body_snippet) < %s)
                ORDER BY article_id DESC
                LIMIT %s
            """, (LOOKBACK_DAYS, MIN_EXISTING_BODY, BATCH_SIZE))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task7] No articles need body fetch.")
        return 0

    log.info(f"[Task7] Fetching {len(rows)} article bodies...")

    enriched = 0
    failed = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, url in rows:
                body = fetch_body(url)
                if not body:
                    failed += 1
                    continue
                cur.execute(
                    "UPDATE articles SET body_snippet = %s WHERE article_id = %s",
                    (body[:MAX_BODY_CHARS], article_id),
                )
                enriched += 1
            conn.commit()

    log.info(f"[Task7] Complete — {enriched} enriched, {failed} failed/blocked")
    return enriched

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
import trafilatura
from bs4 import BeautifulSoup

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

# Targets per cycle. Bigger batches = longer cycles + more 429 risk.
BATCH_SIZE = 60
MIN_EXISTING_BODY = 250        # only fetch if current snippet shorter than this
MAX_BODY_CHARS    = 1500       # truncation target for storage
FETCH_TIMEOUT_S   = 10
LOOKBACK_DAYS     = 14         # backfill 2 weeks of history

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
    """Article body extraction. Tries trafilatura (purpose-built for news)
    first, then falls back to BeautifulSoup heuristics for edge cases."""
    # 1) trafilatura — favor_precision drops nav/related/promo clutter that
    # was leaking into the output (e.g., "Recommended Stories list of 3...")
    try:
        text = trafilatura.extract(
            html,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
            include_links=False,
        )
        if text:
            text = " ".join(text.split())
            if len(text) >= 200:
                return text
    except Exception:
        pass

    # 2) Fallback — BS4 with cluster-of-paragraphs heuristic
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    for t in soup(JUNK_TAGS):
        t.decompose()
    for t in soup.find_all(True):
        if _is_junk(t):
            t.decompose()

    for selector in ["article", "main", '[itemprop="articleBody"]',
                     ".article-body", ".story-body", ".article__content",
                     ".post-content", ".entry-content"]:
        node = soup.select_one(selector)
        if node:
            text = _node_text(node)
            if len(text) >= 200:
                return text

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
            # Prioritize articles that are actually being shown in conflicts or
            # candidate pairs — those are the ones the user clicks into.
            # Then fall back to newest articles within the lookback window.
            cur.execute("""
                WITH in_use AS (
                    SELECT article_a_id AS article_id FROM conflicts
                    UNION
                    SELECT article_b_id FROM conflicts
                    UNION
                    SELECT article_id_1 FROM article_pairs
                    UNION
                    SELECT article_id_2 FROM article_pairs
                )
                SELECT a.article_id, a.url
                FROM articles a
                LEFT JOIN in_use u ON u.article_id = a.article_id
                WHERE a.url IS NOT NULL
                  AND a.fetched_at > NOW() - INTERVAL '%s days'
                  AND (a.body_snippet IS NULL OR LENGTH(a.body_snippet) < %s)
                ORDER BY
                    CASE WHEN u.article_id IS NOT NULL THEN 0 ELSE 1 END,
                    a.article_id DESC
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

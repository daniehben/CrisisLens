"""Telegram channel ingestion via the public t.me/s/<channel> web view.

Why this approach over Telethon (MTProto) or Bot API:
  * MTProto: Render Frankfurt IPs are blocked.
  * Bot API: bots can only read channels they admin — we don't own these.
  * t.me/s/: Telegram's anonymous public web preview. No auth, blocked-egress-friendly.

Each channel returns the most recent ~20 messages parsed from HTML.

Rate limiting:
  Telegram doesn't publish hard limits for t.me/s/ scraping, but they do
  enforce soft limits (~1 req/s sustained, harder on bursts). We protect
  against IP blocks with:
    1. A per-instance inter-request delay (FETCH_DELAY_S between channels
       when the worker fetches all adapters concurrently).
    2. Exponential backoff on 429 / 5xx responses.
    3. Jitter on the delay so multiple workers don't synchronise.
"""
import hashlib
import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from backend.ingestion_worker.adapters.base import FeedAdapter
from backend.shared.models import RawArticle

log = logging.getLogger(__name__)

# Seconds to sleep before each fetch — reduces burst rate when the worker
# runs all adapters near-simultaneously via ThreadPoolExecutor.
FETCH_DELAY_S = 2.0          # base delay
FETCH_DELAY_JITTER = 1.0     # random extra 0–1s on top of base

# Retry config on 429 / 5xx
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 5       # seconds; doubles each retry (5, 10)

# Per-source config: channel username + language + trust weight.
TELEGRAM_SOURCES = {
    'AJA+':  {'channel': 'ajplusar',             'language': 'ar', 'trust_weight': 0.50},
    'WM':    {'channel': 'WarMonitor1',          'language': 'en', 'trust_weight': 0.25},
    'SI':    {'channel': 'spectatorindex',       'language': 'en', 'trust_weight': 0.10},
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en,ar;q=0.9',
}


def _parse_iso(s: str) -> datetime:
    """Telegram time tags use ISO 8601 with TZ offset."""
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _msg_id_from_url(url: str) -> Optional[str]:
    """Extract numeric message id from a t.me/<channel>/<id> URL."""
    if not url:
        return None
    parts = url.rstrip('/').split('/')
    return parts[-1] if parts else None


def _fetch_with_backoff(url: str) -> Optional[str]:
    """GET url with exponential backoff on 429 / 5xx. Returns HTML or None."""
    delay = FETCH_DELAY_S + random.uniform(0, FETCH_DELAY_JITTER)
    time.sleep(delay)

    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                resp = client.get(url)

            if resp.status_code == 200:
                return resp.text

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                log.warning(f"[telegram] 429 rate limited on {url} — backing off {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                log.warning(f"[telegram] {resp.status_code} server error on {url} — backing off {wait}s")
                time.sleep(wait)
                continue

            # 4xx other than 429 — not retryable
            log.warning(f"[telegram] {resp.status_code} on {url} — skipping")
            return None

        except Exception as e:
            wait = RETRY_BACKOFF_BASE * (2 ** attempt)
            log.warning(f"[telegram] request error on {url}: {e} — backing off {wait}s")
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    log.warning(f"[telegram] all retries exhausted for {url}")
    return None


class TelegramWebAdapter(FeedAdapter):
    def __init__(self, code: str):
        if code not in TELEGRAM_SOURCES:
            raise ValueError(f"Unknown Telegram source code: {code}")
        self._code = code
        self._cfg = TELEGRAM_SOURCES[code]

    def source_code(self) -> str:
        return self._code

    def fetch(self) -> list[RawArticle]:
        articles: list[RawArticle] = []
        channel = self._cfg['channel']
        url = f"https://t.me/s/{channel}"

        html = _fetch_with_backoff(url)
        if not html:
            return articles

        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            log.warning(f"[{self._code}] HTML parse failed: {e}")
            return articles

        messages = soup.find_all('div', class_='tgme_widget_message')
        if not messages:
            log.info(f"[{self._code}] No messages found on t.me/s/{channel}")
            return articles

        lang = self._cfg['language']
        for msg in messages[-30:]:
            try:
                text_el = msg.find('div', class_='tgme_widget_message_text')
                if not text_el:
                    continue
                text = text_el.get_text(separator=' ', strip=True)
                text = ' '.join(text.split())
                if len(text) < 30:
                    continue
                low = text.lower()
                if any(x in low for x in ('subscribe', 'forwarded from', '@')) and len(text) < 80:
                    continue

                date_el = msg.find('a', class_='tgme_widget_message_date')
                msg_url = date_el.get('href') if date_el else None
                if not msg_url:
                    continue
                msg_id = _msg_id_from_url(msg_url)
                if not msg_id:
                    continue

                time_el = msg.find('time')
                published = _parse_iso(time_el.get('datetime')) if time_el and time_el.get('datetime') else datetime.utcnow()

                headline = text.split('\n')[0][:300]
                external_id = hashlib.md5(f"{channel}_{msg_id}".encode()).hexdigest()

                article = RawArticle(
                    source_code=self._code,
                    external_id=external_id,
                    url=msg_url,
                    published_at=published,
                    language=lang,
                    trust_weight=self._cfg['trust_weight'],
                    headline_ar=headline if lang == 'ar' else None,
                    headline_en=headline if lang == 'en' else None,
                    body_snippet=text[:1500],
                )
                articles.append(article)
            except Exception as e:
                log.debug(f"[{self._code}] skipping message: {e}")
                continue

        log.info(f"[{self._code}] Fetched {len(articles)} messages from Telegram")
        return articles

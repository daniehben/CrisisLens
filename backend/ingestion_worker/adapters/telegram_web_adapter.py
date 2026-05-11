"""Telegram channel ingestion via the public t.me/s/<channel> web view.

Why this approach over Telethon (MTProto) or Bot API:
  * MTProto: Render Frankfurt IPs are blocked.
  * Bot API: bots can only read channels they admin — we don't own these.
  * t.me/s/: Telegram's anonymous public web preview. No auth, no rate limit
    issues on our scale, blocked-egress-friendly.

Each channel returns the most recent ~20 messages parsed from HTML.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from backend.ingestion_worker.adapters.base import FeedAdapter
from backend.shared.models import RawArticle

log = logging.getLogger(__name__)

# Per-source config: channel username + language + trust weight.
TELEGRAM_SOURCES = {
    'BNO':   {'channel': 'BNOFeed',              'language': 'en', 'trust_weight': 0.50},
    'AJA+':  {'channel': 'ajplusar',             'language': 'ar', 'trust_weight': 0.50},
    'AJE+':  {'channel': 'aje_news',             'language': 'en', 'trust_weight': 0.80},
    'REU':   {'channel': 'reuters_news_agency',  'language': 'en', 'trust_weight': 0.80},
    'BBC+':  {'channel': 'BBCNews_Breaking',     'language': 'en', 'trust_weight': 0.80},
    'WM':    {'channel': 'WarMonitor1',          'language': 'en', 'trust_weight': 0.25},
    'SI':    {'channel': 'spectatorindex',       'language': 'en', 'trust_weight': 0.10},
    'MAYE':  {'channel': 'AlMayadeenEnglish',    'language': 'en', 'trust_weight': 0.45},
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
        # Strip subsecond if present, normalize timezone
        return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _msg_id_from_url(url: str) -> Optional[str]:
    """Extract numeric message id from a t.me/<channel>/<id> URL."""
    if not url:
        return None
    parts = url.rstrip('/').split('/')
    return parts[-1] if parts else None


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

        try:
            with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            log.warning(f"[{self._code}] t.me fetch failed: {e}")
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
        # Walk newest-first
        for msg in messages[-30:]:  # last 30 messages shown
            try:
                text_el = msg.find('div', class_='tgme_widget_message_text')
                if not text_el:
                    continue
                text = text_el.get_text(separator=' ', strip=True)
                text = ' '.join(text.split())  # collapse whitespace
                if len(text) < 30:
                    continue
                # Skip obvious channel chrome
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

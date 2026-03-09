import hashlib
from datetime import datetime
from backend.ingestion_worker.adapters.base import FeedAdapter
from backend.shared.models import RawArticle
from backend.shared.config import config

# Telegram channel usernames mapped to our source codes
TELEGRAM_SOURCES = {
    'BNO':  {'channel': 'BNOFeed',             'language': 'en', 'trust_weight': 0.50},
    'AJA+': {'channel': 'ajplusar',             'language': 'ar', 'trust_weight': 0.50},
    'AJE+': {'channel': 'aje_news',             'language': 'en', 'trust_weight': 0.80},
    'REU':  {'channel': 'reuters_news_agency',  'language': 'en', 'trust_weight': 0.80},
    'BBC+': {'channel': 'BBCNews_Breaking',     'language': 'en', 'trust_weight': 0.80},
    'WM':   {'channel': 'WarMonitor1',          'language': 'en', 'trust_weight': 0.25},
    'SI':   {'channel': 'spectatorindex',       'language': 'en', 'trust_weight': 0.10},
}


def _make_external_id(channel: str, msg_id: int) -> str:
    return hashlib.md5(f"{channel}_{msg_id}".encode()).hexdigest()


def _make_url(channel: str, msg_id: int) -> str:
    return f"https://t.me/{channel}/{msg_id}"


class TelegramAdapter(FeedAdapter):
    """
    Fetches messages from Telegram channels using Telethon MTProto client.
    Requires a valid session file (created via one-time interactive login).

    NOTE: The session file must exist at backend/ingestion_worker/telegram.session
    Run setup_telegram_session.py once to create it.
    """

    def __init__(self, code: str):
        if code not in TELEGRAM_SOURCES:
            raise ValueError(f"Unknown Telegram source code: {code}")
        self._code = code
        self._source_config = TELEGRAM_SOURCES[code]

    def source_code(self) -> str:
        return self._code

    def fetch(self) -> list[RawArticle]:
        # Import here to avoid errors if telethon not installed
        try:
            from telethon.sync import TelegramClient
        except ImportError:
            print(f"[{self._code}] Telethon not installed, skipping")
            return []

        articles = []
        channel = self._source_config['channel']
        lang = self._source_config['language']

        try:
            import os
            session_path = os.path.join(
                os.path.dirname(__file__), '..', 'telegram'
            )

            with TelegramClient(session_path, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH) as client:
                messages = client.get_messages(channel, limit=30)

                for msg in messages:
                    if not msg.text or len(msg.text.strip()) < 20:
                        continue
                    if 'copyright' in msg.text.lower() or "couldn't be displayed" in msg.text.lower():
                        continue
                    try:
                        text = msg.text.strip()
                        headline = text.split('\n')[0][:300]

                        article = RawArticle(
                            source_code=self._code,
                            external_id=_make_external_id(channel, msg.id),
                            url=_make_url(channel, msg.id),
                            published_at=msg.date.replace(tzinfo=None),
                            language=lang,
                            trust_weight=self._source_config['trust_weight'],
                            headline_ar=headline if lang == 'ar' else None,
                            headline_en=headline if lang == 'en' else None,
                            body_snippet=text[:500],
                        )
                        articles.append(article)

                    except Exception as e:
                        print(f"[{self._code}] Skipping message {msg.id}: {e}")
                        continue

            print(f"[{self._code}] Fetched {len(articles)} messages")

        except Exception as e:
            print(f"[{self._code}] Fetch failed: {e}")

        return articles
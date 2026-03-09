import hashlib
import httpx
import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from backend.ingestion_worker.adapters.base import FeedAdapter
from backend.shared.models import RawArticle


RSS_SOURCES = {
    'AJA': {
        'url': 'https://www.aljazeera.com/xml/rss/all.xml',
        'language': 'en',  # temporarily English until DACR credentials arrive
        'trust_weight': 1.00,
    },
     'ASH': {
        'url': 'https://aawsat.com/feed',
        'language': 'ar',
        'trust_weight': 0.65,
    },
     'TNA': {
        'url': 'https://www.newarab.com/rss',
        'language': 'en',
        'trust_weight': 0.65,
    },
}

# Mimic a real browser to avoid being blocked
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Accept-Language': 'ar,en;q=0.9',
}


def _parse_date(entry) -> datetime:
    try:
        if hasattr(entry, 'published'):
            return parsedate_to_datetime(entry.published).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.utcnow()


def _make_external_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


class RSSAdapter(FeedAdapter):

    def __init__(self, code: str):
        if code not in RSS_SOURCES:
            raise ValueError(f"Unknown RSS source code: {code}")
        self._code = code
        self._config = RSS_SOURCES[code]

    def source_code(self) -> str:
        return self._code

    def fetch(self) -> list[RawArticle]:
        articles = []
        try:
            # Fetch raw content with browser headers first
            with httpx.Client(timeout=20, headers=HEADERS, follow_redirects=True) as client:
                response = client.get(self._config['url'])
                response.raise_for_status()
                raw_content = response.content

            # Parse the fetched content
            feed = feedparser.parse(raw_content)

            if not feed.entries:
                print(f"[{self._code}] No entries found in feed")
                return []

            lang = self._config['language']

            for entry in feed.entries[:50]:
                try:
                    url = entry.get('link', '').strip()
                    if not url:
                        continue

                    title = entry.get('title', '').strip()
                    summary = entry.get('summary', '').strip()

                    if not title:
                        continue

                    article = RawArticle(
                        source_code=self._code,
                        external_id=_make_external_id(url),
                        url=url,
                        published_at=_parse_date(entry),
                        language=lang,
                        trust_weight=self._config['trust_weight'],
                        headline_ar=title if lang == 'ar' else None,
                        headline_en=title if lang == 'en' else None,
                        body_snippet=summary[:500] if summary else None,
                    )
                    articles.append(article)

                except Exception as e:
                    print(f"[{self._code}] Skipping entry: {e}")
                    continue

            print(f"[{self._code}] Fetched {len(articles)} articles")

        except httpx.HTTPStatusError as e:
            print(f"[{self._code}] HTTP error {e.response.status_code}: {self._config['url']}")
        except Exception as e:
            print(f"[{self._code}] Fetch failed: {e}")

        return articles
import hashlib
import httpx
from datetime import datetime, timezone
from backend.ingestion_worker.adapters.base import FeedAdapter
from backend.shared.models import RawArticle
from backend.shared.config import config

NEWSAPI_BASE = 'https://newsapi.org/v2/top-headlines'

# NewsAPI source IDs mapped to our source codes
NEWSAPI_SOURCES = {
    'AJE':  {'newsapi_id': 'al-jazeera-english',   'language': 'en', 'trust_weight': 0.80},
    'BBC':  {'newsapi_id': 'bbc-news',              'language': 'en', 'trust_weight': 0.80},
    'JRP':  {'newsapi_id': 'the-jerusalem-post',    'language': 'en', 'trust_weight': 0.75},
    'WP':   {'newsapi_id': 'the-washington-post',   'language': 'en', 'trust_weight': 0.80},
    'AP':   {'newsapi_id': 'associated-press',      'language': 'en', 'trust_weight': 0.80},
}


def _make_external_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


class NewsAPIAdapter(FeedAdapter):
    """
    Fetches articles from NewsAPI.org.
    Covers: AJE, BBC, Reuters, AP.
    Batches all sources into one API call to conserve daily quota (100 req/day free).
    """

    def __init__(self, code: str):
        if code not in NEWSAPI_SOURCES:
            raise ValueError(f"Unknown NewsAPI source code: {code}")
        self._code = code
        self._source_config = NEWSAPI_SOURCES[code]

    def source_code(self) -> str:
        return self._code

    def fetch(self) -> list[RawArticle]:
        articles = []
        try:
            params = {
                'sources': self._source_config['newsapi_id'],
                'pageSize': 20,
                'apiKey': config.NEWSAPI_KEY,
            }

            with httpx.Client(timeout=15) as client:
                response = client.get(NEWSAPI_BASE, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get('status') != 'ok':
                print(f"[{self._code}] NewsAPI error: {data.get('message')}")
                return []

            for item in data.get('articles', []):
                try:
                    url = item.get('url', '')
                    if not url or url == 'https://removed.com':
                        continue

                    title = (item.get('title') or '').strip()
                    description = (item.get('description') or '').strip()

                    if not title:
                        continue

                    article = RawArticle(
                        source_code=self._code,
                        external_id=_make_external_id(url),
                        url=url,
                        published_at=_parse_date(item.get('publishedAt', '')),
                        language=self._source_config['language'],
                        trust_weight=self._source_config['trust_weight'],
                        headline_en=title,
                        body_snippet=description[:500] if description else None,
                    )
                    articles.append(article)

                except Exception as e:
                    print(f"[{self._code}] Skipping article: {e}")
                    continue

            print(f"[{self._code}] Fetched {len(articles)} articles")

        except Exception as e:
            print(f"[{self._code}] Fetch failed: {e}")

        return articles
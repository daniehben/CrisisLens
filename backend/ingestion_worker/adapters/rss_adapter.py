import hashlib
import re
import httpx
import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from backend.ingestion_worker.adapters.base import FeedAdapter
from backend.shared.models import RawArticle

_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


RSS_SOURCES = {
    'AJA': {
        'url': 'https://www.aljazeera.com/xml/rss/all.xml',
        'language': 'en',  # temporarily English until DACR credentials arrive
        'trust_weight': 1.00,
    },
    # AJA+ disabled — RSSHub bridge hits Telegram rate limit (429) on free tier.
    # See docs/BACKLOG.md to re-enable once we have a paid RSSHub or alternative.
    # 'AJA+': {
    #     'url': 'https://crisislens-rsshub.onrender.com/telegram/channel/ajplusar',
    #     'language': 'ar',
    #     'trust_weight': 0.50,
    # },
    # ── Arabic-language broadcasters accessible from Frankfurt ─────────────
    'DW': {
        'url': 'https://rss.dw.com/rdf/rss-ar-all',
        'language': 'ar',
        'trust_weight': 0.80,  # German state broadcaster
    },
    'F24': {
        # Direct france24.com blocks Render Frankfurt IPs (403). Route through
        # Google News like ARB.
        'url': 'https://news.google.com/rss/search?q=site:france24.com/ar&hl=ar&gl=FR&ceid=FR:ar',
        'language': 'ar',
        'trust_weight': 0.80,  # French international broadcaster
    },
    'ARB': {
        # Direct alarabiya.net feed blocks Render Frankfurt IPs (403).
        'url': 'https://news.google.com/rss/search?q=site:alarabiya.net&hl=ar&gl=SA&ceid=SA:ar',
        'language': 'ar',
        'trust_weight': 0.65,  # Saudi-aligned
    },
    # ── Palestinian perspective ────────────────────────────────────────────
    'MND': {
        'url': 'https://mondoweiss.net/feed/',
        'language': 'en',
        'trust_weight': 0.55,  # US-based Palestinian-rights advocacy
    },
    'WAF': {
        # WAFA — Official Palestinian News Agency. Direct /rss returns 404,
        # routing through Google News.
        'url': 'https://news.google.com/rss/search?q=site:wafa.ps+OR+site:english.wafa.ps&hl=en&gl=PS&ceid=PS:en',
        'language': 'en',
        'trust_weight': 0.65,
    },
    'AKH': {
        # Al-Akhbar (Lebanese, anti-Western framing)
        'url': 'https://news.google.com/rss/search?q=site:al-akhbar.com&hl=ar&gl=LB&ceid=LB:ar',
        'language': 'ar',
        'trust_weight': 0.55,
    },
    # ── State media counter-Western perspective ────────────────────────────
    'TAS': {
        # Tasnim (Iranian state, Arabic edition)
        'url': 'https://news.google.com/rss/search?q=site:tasnimnews.com/ar&hl=ar&gl=IR&ceid=IR:ar',
        'language': 'ar',
        'trust_weight': 0.40,
    },
    'PTV': {
        # Press TV (Iranian state, English)
        'url': 'https://news.google.com/rss/search?q=site:presstv.ir&hl=en&gl=IR&ceid=IR:en',
        'language': 'en',
        'trust_weight': 0.40,
    },
    'RTA': {
        # RT Arabic (Russian state, Arabic) — banned in EU but Google News still indexes
        'url': 'https://news.google.com/rss/search?q=site:arabic.rt.com&hl=ar&gl=RU&ceid=RU:ar',
        'language': 'ar',
        'trust_weight': 0.35,
    },
    # ── Turkish ────────────────────────────────────────────────────────────
    'ANA': {
        # Anadolu Agency (Turkish state, Arabic)
        'url': 'https://www.aa.com.tr/ar/rss/default?cat=guncel',
        'language': 'ar',
        'trust_weight': 0.70,  # State-owned but professional
    },
    # ── Independent voices (Substack / blogs) ──────────────────────────────
    'GG': {
        # Glenn Greenwald — investigative, anti-establishment
        'url': 'https://greenwald.substack.com/feed',
        'language': 'en',
        'trust_weight': 0.50,
    },
    'GZ': {
        # The Grayzone — Max Blumenthal, Aaron Maté
        'url': 'https://thegrayzone.com/feed/',
        'language': 'en',
        'trust_weight': 0.40,
    },
    'CJ': {
        # Caitlin Johnstone — Australian anti-imperialist
        'url': 'https://caitlinjohnstone.substack.com/feed',
        'language': 'en',
        'trust_weight': 0.35,
    },
    'EI': {
        # Electronic Intifada — Palestinian advocacy
        'url': 'https://electronicintifada.net/rss.xml',
        'language': 'en',
        'trust_weight': 0.55,
    },
    'AW': {
        # Antiwar.com — direct RSS returned 0 entries; routing via Google News
        'url': 'https://news.google.com/rss/search?q=site:antiwar.com&hl=en&gl=US&ceid=US:en',
        'language': 'en',
        'trust_weight': 0.45,
    },
    'CRA': {
        # The Cradle — West Asia focused
        'url': 'https://thecradle.co/feed/',
        'language': 'en',
        'trust_weight': 0.45,
    },
    'DSN': {
        # Drop Site News — Ryan Grim, ex-Intercept
        'url': 'https://www.dropsitenews.com/feed',
        'language': 'en',
        'trust_weight': 0.55,
    },
    # ── Former NewsAPI sources → migrated to RSS (2026-06) ───────────────
    # NewsAPI free/dev plan cannot be used in production (ToS violation).
    # All sources now served via direct RSS or Google News proxy.
    'BBC': {
        # BBC World News — direct official feed
        'url': 'https://feeds.bbci.co.uk/news/world/rss.xml',
        'language': 'en',
        'trust_weight': 0.80,
    },
    'REU': {
        # Reuters — no public RSS since 2020; route via Google News
        'url': 'https://news.google.com/rss/search?q=site:reuters.com&hl=en&gl=US&ceid=US:en',
        'language': 'en',
        'trust_weight': 0.85,
    },
    'AP': {
        # Associated Press — official AP top news RSS
        'url': 'https://feeds.apnews.com/rss/apf-topnews',
        'language': 'en',
        'trust_weight': 0.80,
    },
    'WP': {
        # Washington Post — direct RSS paywalled; route via Google News
        'url': 'https://news.google.com/rss/search?q=site:washingtonpost.com&hl=en&gl=US&ceid=US:en',
        'language': 'en',
        'trust_weight': 0.75,
    },
    'JRP': {
        # Jerusalem Post — via Google News (direct feed intermittent)
        'url': 'https://news.google.com/rss/search?q=site:jpost.com&hl=en&gl=IL&ceid=IL:en',
        'language': 'en',
        'trust_weight': 0.70,
    },
    # AJE (Al Jazeera English via NewsAPI) → dropped; same content as AJA (trust 1.0)
    # ── BNO News: moved from Telegram scraping to direct RSS ─────────────
    'BNO': {
        # BNO News breaking news feed — official RSS, no scraping needed
        'url': 'https://bnonews.com/index.php/feed/',
        'language': 'en',
        'trust_weight': 0.50,
    },
    # ── Al Mayadeen: moved from Telegram scraping to direct RSS ──────────
    'MAYE': {
        # Al Mayadeen English — official RSS
        'url': 'https://www.almayadeen.net/rss/all.xml',
        'language': 'en',
        'trust_weight': 0.45,
    },
    # ── New global sources (2026-05) ──────────────────────────────────────
    'CNN': {
        # CNN — via Google News (direct cnn.com blocks Render Frankfurt IPs)
        'url': 'https://news.google.com/rss/search?q=site:cnn.com&hl=en&gl=US&ceid=US:en',
        'language': 'en',
        'trust_weight': 0.75,
    },
    'GUA': {
        # The Guardian — world news RSS (direct, usually accessible)
        'url': 'https://www.theguardian.com/world/rss',
        'language': 'en',
        'trust_weight': 0.78,
    },
    'BBAR': {
        # BBC Arabic — same trust as BBC English, Arabic edition
        'url': 'http://feeds.bbci.co.uk/arabic/rss.xml',
        'language': 'ar',
        'trust_weight': 0.80,
    },
    'SKA': {
        # Sky News Arabia — UAE/Saudi aligned, good non-Palestine coverage
        'url': 'https://news.google.com/rss/search?q=site:skynewsarabia.com&hl=ar&gl=AE&ceid=AE:ar',
        'language': 'ar',
        'trust_weight': 0.65,
    },
    'MEE': {
        # Middle East Eye — independent UK-based, strong MENA coverage
        'url': 'https://news.google.com/rss/search?q=site:middleeasteye.net&hl=en&gl=GB&ceid=GB:en',
        'language': 'en',
        'trust_weight': 0.60,
    },
    'SDT': {
        # Sudan Tribune — geography expansion: Sudan, Horn of Africa, Sahel
        'url': 'https://sudantribune.com/feed/',
        'language': 'en',
        'trust_weight': 0.60,
    },
    # ── YouTube commentary channels via RSS ────────────────────────────────
    # YouTube exposes per-channel RSS at:
    #   https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>
    'YT_BP': {
        # Breaking Points (Krystal Ball & Saagar Enjeti)
        'url': 'https://www.youtube.com/feeds/videos.xml?channel_id=UCDRIjKy6eZOvKtOELtTdeUA',
        'language': 'en',
        'trust_weight': 0.35,
    },
    'YT_DN': {
        # Democracy Now!
        'url': 'https://www.youtube.com/feeds/videos.xml?channel_id=UCzuqE7-t13O4NIDYJfakrhw',
        'language': 'en',
        'trust_weight': 0.50,
    },
    # YT_GZ disabled — channel ID was wrong (404). Grayzone content already
    # ingested via their website RSS (code: GZ).
    # 'YT_GZ': {
    #     'url': 'https://www.youtube.com/feeds/videos.xml?channel_id=...',
    # },
    'YT_RT': {
        # The Real News Network
        'url': 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYwlraEwuFB4ZqASowjoM0g',
        'language': 'en',
        'trust_weight': 0.45,
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


def _extract_image(entry) -> str | None:
    """Try the common RSS image locations feedparser exposes."""
    # media:thumbnail (YouTube, many news feeds)
    thumbs = getattr(entry, 'media_thumbnail', None)
    if thumbs and isinstance(thumbs, list):
        url = thumbs[0].get('url', '')
        if url:
            return url
    # media:content with medium=image
    content = getattr(entry, 'media_content', None)
    if content and isinstance(content, list):
        for item in content:
            if item.get('medium') == 'image' or item.get('type', '').startswith('image/'):
                url = item.get('url', '')
                if url:
                    return url
    # <enclosure> with image mime type
    for enc in getattr(entry, 'enclosures', []):
        if enc.get('type', '').startswith('image/'):
            url = enc.get('href') or enc.get('url', '')
            if url:
                return url
    # <img src> embedded in content or summary HTML
    for field in ('content', 'summary'):
        html = ''
        val = getattr(entry, field, None)
        if not val:
            continue
        if isinstance(val, list):
            html = ' '.join(v.get('value', '') for v in val if isinstance(v, dict))
        elif isinstance(val, str):
            html = val
        m = _IMG_TAG_RE.search(html)
        if m:
            url = m.group(1)
            # Skip tiny tracking pixels and SVG data URIs
            if url.startswith('data:') or (url.endswith('.gif') and 'pixel' in url.lower()):
                continue
            return url
    return None


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
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), headers=HEADERS, follow_redirects=True) as client:
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
                        image_url=_extract_image(entry),
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
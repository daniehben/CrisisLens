import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from backend.shared.deduplication import check_and_mark
from backend.shared.database import get_db_connection, get_source_map
from backend.ingestion_worker.db_writer import write_batch
from backend.ingestion_worker.adapters.rss_adapter import RSSAdapter
from backend.ingestion_worker.adapters.telegram_web_adapter import TelegramWebAdapter
# NewsAPIAdapter removed — free/dev plan cannot be used in production (ToS violation).
# All former NewsAPI sources (BBC, REU, AP, WP, JRP) now served via RSS.


# ──────────────────────────────────────────────────────────────────────────────
# Topic filter — Evolution Step 1
# Keep only Palestine · Israel · Lebanon · Iran · Regional Spillover content.
# Blocks off-topic articles (Bangladesh cricket, Sudan general news, etc.)
# before they ever hit the DB or NLP pipeline.
# ──────────────────────────────────────────────────────────────────────────────

# Sources whose entire editorial scope is our focus — no keyword check needed.
_ALWAYS_RELEVANT_SOURCES = {
    'WAF',   # WAFA Palestinian news agency — 100 % Palestine
    'MND',   # Mondoweiss — I-P conflict focus
    'EI',    # Electronic Intifada
    'JRP',   # Jerusalem Post
    'AKH',   # Al-Akhbar Lebanon
}

# English keyword regex — any match → article is on-topic.
_TOPIC_RE_EN = re.compile(
    r'\b(?:'
    # Palestinian / Israeli geography & entities
    r'palest\w+|israel\w*|'
    r'gaza|west\s+bank|jenin|nablus|ramallah|hebron|tulkarm|'
    r'rafah|khan\s+younis|jabalia|beit\s+lahiya|deir\s+al.balah|'
    r'al.aqsa|temple\s+mount|haifa|tel\s+aviv|jerusalem|golan|'
    # Lebanon
    r'lebanon\w*|beirut|south\s+lebanon|hezbollah|'
    # Iran
    r'iran\w*|tehran|irgc|khuzestan|'
    # Yemen / Houthis
    r'houthi\w*|ansar\s+allah|yemeni?|sanaa|'
    # Iraqi PMF / Axis of Resistance
    r'kataib|hashd|popular\s+mobilization|axis\s+of\s+resistance|'
    # Key actors
    r'hamas|islamic\s+jihad|nasrallah|sinwar|haniyeh|'
    r'khamenei|netanyahu|idf\b|iof\b|plo\b|fatah\b|'
    # Conflict vocabulary
    r'occupation|occupied|settler\w*|settlements?|ceasefire|'
    r'intifada|apartheid|blockade|siege|hostage\w*|'
    r'rafah\s+crossing|kerem\s+shalom|'
    r'red\s+sea|strait\s+of\s+hormuz'
    r')\b',
    re.IGNORECASE,
)

# Arabic terms — substring match against Arabic headline.
_TOPIC_TERMS_AR = (
    'فلسطين', 'فلسطيني', 'إسرائيل', 'غزة', 'الضفة', 'جنين', 'نابلس',
    'رام الله', 'الخليل', 'طولكرم', 'رفح', 'خانيونس', 'الأقصى', 'القدس',
    'لبنان', 'بيروت', 'حزب الله',
    'إيران', 'طهران', 'الحرس الثوري',
    'الحوثي', 'أنصار الله', 'اليمن',
    'حماس', 'الجهاد الإسلامي',
    'نصر الله', 'نتنياهو', 'خامنئي', 'سنوار',
    'المقاومة', 'الاحتلال', 'المستوطنات', 'وقف إطلاق النار',
    'البحر الأحمر', 'الأسرى', 'الرهائن',
)


def _is_relevant(article) -> bool:
    """True if the article is on-topic for CrisisLens's focus:
    Palestine · Israel · Lebanon · Iran · Regional Spillover.

    Sources in _ALWAYS_RELEVANT_SOURCES bypass the check — their entire scope
    already matches. For all others, at least one keyword must appear in
    headline_en, the first 200 chars of body_snippet, or headline_ar.
    """
    if article.source_code in _ALWAYS_RELEVANT_SOURCES:
        return True
    # English / snippet check
    en_text = ' '.join(filter(None, [
        article.headline_en,
        (article.body_snippet or '')[:200],
    ]))
    if en_text and _TOPIC_RE_EN.search(en_text):
        return True
    # Arabic headline check (substring — word boundaries don't work cleanly in Arabic regex)
    ar_text = article.headline_ar or ''
    if ar_text:
        for term in _TOPIC_TERMS_AR:
            if term in ar_text:
                return True
    return False


# Cap concurrent fetches. Tradeoff: higher = faster cycle, lower = less memory.
# Render free tier is 512MB; each adapter holds HTML + parsed feed in memory.
MAX_CONCURRENT_FETCHES = 3

# Telegram adapters are module-level singletons so their watermark (highest
# seen message ID) persists across cycles within the same process. RSS adapters
# are recreated each cycle — they're stateless and the global check_and_mark
# set handles intra-process dedup for them.
_TELEGRAM_ADAPTERS = [TelegramWebAdapter(code) for code in ['AJA+', 'WM', 'SI']]


def get_all_adapters():
    rss_adapters = [RSSAdapter(code) for code in [
        # ── Core Arabic-first sources ──────────────────────────────────────
        'AJA',                              # Al Jazeera Arabic (trust 0.90)
        'DW', 'F24', 'ARB',                # Arabic broadcasters
        'BBAR', 'SKA',                      # BBC Arabic, Sky News Arabia
        # ── Palestinian / resistance perspective ──────────────────────────
        'MND', 'WAF', 'AKH', 'EI', 'PCH', 'IMEMC',
        # ── State media ───────────────────────────────────────────────────
        'TAS', 'PTV', 'RTA', 'ANA',
        # ── Western mainstream (now via RSS, not NewsAPI) ─────────────────
        'BBC', 'REU', 'AP', 'WP', 'JRP',   # former NewsAPI sources
        'CNN', 'GUA', 'MEE', 'SDT',
        # ── Independent voices ────────────────────────────────────────────
        'GG', 'GZ', 'CJ', 'AW', 'CRA', 'DSN',
        # ── Breaking news / aggregators ───────────────────────────────────
        'BNO', 'MAYE',
        # ── YouTube commentary ────────────────────────────────────────────
        'YT_BP', 'YT_DN', 'YT_RT',
        # ── Activations (in DB, now wired in) ─────────────────────────────
        'HAA', 'TNA', 'ASH',   # MAN removed 2026-08: not in Google News index
    ]]
    return rss_adapters + _TELEGRAM_ADAPTERS


def log_ingestion(conn, source_code: str, fetched: int, inserted: int,
                  duplicates: int, errors: int, duration_ms: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_id FROM sources WHERE code = %s", (source_code,))
            row = cur.fetchone()
            if not row:
                return
            status = 'error' if errors > 0 else 'ok'
            cur.execute("""
                INSERT INTO ingestion_logs
                    (source_id, articles_fetched, articles_new,
                     articles_duped, duration_ms, status, run_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                row[0], fetched, inserted, duplicates,
                duration_ms, status,
                datetime.now(timezone.utc).replace(tzinfo=None)
            ))
    except Exception as e:
        print(f"[worker] Failed to log ingestion for {source_code}: {e}")


def _fetch_one(adapter):
    """Wrap adapter.fetch() so an exception in one source doesn't kill the pool.
    Returns (code, articles, errors, duration_ms)."""
    code = adapter.source_code()
    start = datetime.now(timezone.utc)
    try:
        articles = adapter.fetch()
        errors = 0
    except Exception as e:
        print(f"[worker] [{code}] Adapter fetch failed: {e}")
        articles = []
        errors = 1
    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    return code, articles, errors, duration_ms


def run_ingestion_cycle() -> None:
    cycle_start = datetime.now(timezone.utc)
    print(f"\n[worker] === Ingestion cycle starting at "
          f"{cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC ===")

    adapters = get_all_adapters()
    source_map = get_source_map()  # cache once per cycle, not per write_batch
    total_fetched = 0
    total_inserted = 0
    total_dupes = 0
    total_filtered = 0

    # Phase 1 — fetch all sources concurrently.
    # I/O-bound work (HTTP to external APIs) → threads are the right tool.
    fetch_results = []
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_FETCHES, len(adapters))) as pool:
        futures = [pool.submit(_fetch_one, a) for a in adapters]
        for future in as_completed(futures):
            fetch_results.append(future.result())

    # Phase 2 — dedup + write serially against a single DB connection.
    # Keeps connection count at 1 and avoids transaction-scope confusion.
    with get_db_connection() as conn:
        for code, articles, errors, fetch_ms in fetch_results:
            fetched = len(articles)
            new_articles = []
            dupes = 0
            filtered = 0
            for article in articles:
                if check_and_mark(article.url):
                    dupes += 1
                elif not _is_relevant(article):
                    filtered += 1
                else:
                    new_articles.append(article)
            inserted, db_skipped = write_batch(new_articles, source_map=source_map)
            dupes += db_skipped
            log_ingestion(conn, code, fetched, inserted, db_skipped, errors, fetch_ms)
            print(f"[worker] [{code}] fetched={fetched} "
                  f"new={inserted} dupes={db_skipped} filtered={filtered} errors={errors} "
                  f"({fetch_ms}ms)")
            total_fetched += fetched
            total_inserted += inserted
            total_dupes += db_skipped
            total_filtered += filtered
        conn.commit()

    cycle_ms = int((datetime.now(timezone.utc) - cycle_start).total_seconds() * 1000)
    print(f"[worker] === Cycle complete in {cycle_ms}ms: "
          f"fetched={total_fetched} inserted={total_inserted} "
          f"dupes={total_dupes} filtered={total_filtered} ===\n")

def run_worker():
    run_ingestion_cycle()
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from backend.shared.deduplication import get_redis_client, check_and_mark
from backend.shared.database import get_db_connection, get_source_map
from backend.ingestion_worker.db_writer import write_batch
from backend.ingestion_worker.adapters.rss_adapter import RSSAdapter
from backend.ingestion_worker.adapters.newsapi_adapter import NewsAPIAdapter
from backend.ingestion_worker.adapters.telegram_adapter import TelegramAdapter


# Cap concurrent fetches so we don't hammer NewsAPI and trip its 100 req/day quota
# all at once (each NewsAPI source is one separate API call).
MAX_CONCURRENT_FETCHES = 6


def get_all_adapters():
    adapters = []
    for code in ['AJA', 'AJA+', 'DW', 'F24', 'ARB']:
        adapters.append(RSSAdapter(code))
    for code in ['AJE', 'BBC', 'JRP', 'WP', 'AP']:
        adapters.append(NewsAPIAdapter(code))
    # Telegram temporarily disabled on Render - fix in next iteration
    # for code in ['BNO', 'AJA+', 'AJE+', 'REU', 'BBC+', 'WM', 'SI']:
    #     adapters.append(TelegramAdapter(code))
    return adapters


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

    r = get_redis_client()  # None if Redis unreachable — handled downstream
    adapters = get_all_adapters()
    source_map = get_source_map()  # cache once per cycle, not per write_batch
    total_fetched = 0
    total_inserted = 0
    total_dupes = 0

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
            for article in articles:
                if check_and_mark(r, article.url):
                    dupes += 1
                else:
                    new_articles.append(article)
            inserted, db_skipped = write_batch(new_articles, source_map=source_map)
            dupes += db_skipped
            log_ingestion(conn, code, fetched, inserted, db_skipped, errors, fetch_ms)
            print(f"[worker] [{code}] fetched={fetched} "
                  f"new={inserted} dupes={db_skipped} errors={errors} "
                  f"({fetch_ms}ms)")
            total_fetched += fetched
            total_inserted += inserted
            total_dupes += db_skipped
        conn.commit()

    cycle_ms = int((datetime.now(timezone.utc) - cycle_start).total_seconds() * 1000)
    print(f"[worker] === Cycle complete in {cycle_ms}ms: "
          f"fetched={total_fetched} inserted={total_inserted} "
          f"dupes={total_dupes} ===\n")

def run_worker():
    run_ingestion_cycle()
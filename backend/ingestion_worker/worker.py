from datetime import datetime, timezone
from backend.shared.deduplication import get_redis_client, check_and_mark
from backend.shared.database import get_db_connection
from backend.ingestion_worker.db_writer import write_batch
from backend.ingestion_worker.adapters.rss_adapter import RSSAdapter
from backend.ingestion_worker.adapters.newsapi_adapter import NewsAPIAdapter
from backend.ingestion_worker.adapters.telegram_adapter import TelegramAdapter


def get_all_adapters():
    adapters = []
    for code in ['AJA']:
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


def run_ingestion_cycle() -> None:
    print(f"\n[worker] === Ingestion cycle starting at "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC ===")
    r = get_redis_client()
    adapters = get_all_adapters()
    total_fetched = 0
    total_inserted = 0
    total_dupes = 0
    with get_db_connection() as conn:
        for adapter in adapters:
            code = adapter.source_code()
            start = datetime.now(timezone.utc)
            errors = 0
            try:
                articles = adapter.fetch()
            except Exception as e:
                print(f"[worker] [{code}] Adapter fetch failed: {e}")
                errors = 1
                articles = []
            fetched = len(articles)
            new_articles = []
            dupes = 0
            for article in articles:
                if check_and_mark(r, article.url):
                    dupes += 1
                else:
                    new_articles.append(article)
            inserted, db_skipped = write_batch(new_articles)
            dupes += db_skipped
            duration_ms = int(
                (datetime.now(timezone.utc) - start).total_seconds() * 1000
            )
            log_ingestion(conn, code, fetched, inserted, dupes, errors, duration_ms)
            print(f"[worker] [{code}] fetched={fetched} "
                  f"new={inserted} dupes={dupes} errors={errors} "
                  f"({duration_ms}ms)")
            total_fetched += fetched
            total_inserted += inserted
            total_dupes += dupes
        conn.commit()
    print(f"[worker] === Cycle complete: "
          f"fetched={total_fetched} inserted={total_inserted} "
          f"dupes={total_dupes} ===\n")


def run_worker():
    run_ingestion_cycle()
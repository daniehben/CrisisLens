import os
import base64
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from backend.ingestion_worker.worker import run_worker
from backend.nlp_pipeline.task7_fetch_body import run_task7
from backend.nlp_pipeline.task7_5_summarize import run_task7_5
from backend.nlp_pipeline.task8_translate import run_task8, run_task8b
from backend.nlp_pipeline.task9_embed import run_task9
from backend.nlp_pipeline.task10_pairs import run_task10
from backend.nlp_pipeline.task11_nli import run_task11
from backend.nlp_pipeline.task12_conflicts import run_task12
from backend.nlp_pipeline.task13_bias_analysis import run_task13
from backend.nlp_pipeline.task14_translate_analysis import run_task14
from backend.nlp_pipeline.task6_images import run_task6
from backend.nlp_pipeline.task15_cleanup import run_task15

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def restore_telegram_session():
    b64 = os.getenv('TELEGRAM_SESSION_B64')
    if not b64:
        print("[scheduler] No TELEGRAM_SESSION_B64 — using existing session file")
        return
    session_path = 'backend/ingestion_worker/telegram.session'
    if os.path.exists(session_path):
        print("[scheduler] Session file already exists — skipping restore")
        return
    with open(session_path, 'wb') as f:
        f.write(base64.b64decode(b64))
    print("[scheduler] Telegram session file restored from env var")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "worker running"}')

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

    def log_message(self, format, *args):
        pass  # silence HTTP logs


def start_health_server():
    port = int(os.getenv('PORT', 8001))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"[scheduler] Health server listening on port {port}")
    server.serve_forever()


def run_ingestion_and_nlp():
    steps = [
        ("worker",  run_worker),
        ("task6",   run_task6),   # OG image backfill — runs right after ingestion
        ("task7",   run_task7),
        ("task7_5", run_task7_5),
        ("task8",   run_task8),
        ("task8b",  run_task8b),
        ("task9",   run_task9),
        ("task10",  run_task10),
        ("task11",  run_task11),
        ("task12",  run_task12),
        ("task13",  run_task13),
        ("task14",  run_task14),
    ]
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            log.error(f"[scheduler] {name} crashed: {e}", exc_info=True)


def run_daily_cleanup():
    """
    Runs once per day at 02:00 UTC — separate from the 15-minute ingestion cycle
    so cleanup never competes with live article insertion.

    Retention window is controlled by ARTICLE_RETENTION_DAYS env var (default 90).
    Change it in Render dashboard without redeploying:

      Tighten (delete sooner) — set ARTICLE_RETENTION_DAYS=30 or 60
        Use when: Supabase storage approaching 400MB, or you want leaner NLP queues.
        Minimum allowed: 14 (hard floor in task15_cleanup.py).

      Loosen (keep longer) — set ARTICLE_RETENTION_DAYS=120 or 180
        Use when: upgraded to Supabase Pro, or researchers need longer history.
        No upper limit enforced — but >180 days on free tier risks storage exhaustion.
    """
    log.info("[scheduler] Running daily cleanup (task15)...")
    try:
        result = run_task15()
        log.info(
            f"[scheduler] Daily cleanup done — "
            f"articles: {result['articles_deleted']}, "
            f"pairs: {result['pairs_deleted']}, "
            f"conflicts: {result['conflicts_deleted']}. "
            f"{result['storage_note']}"
        )
    except Exception as e:
        log.error(f"[scheduler] task15 (cleanup) crashed: {e}", exc_info=True)


def main():
    print("[scheduler] CrisisLens ingestion worker starting...")

    # Fail fast on missing config rather than crash mid-cycle
    from backend.shared.config import Config
    try:
        Config.validate()
    except ValueError as e:
        print(f"[scheduler] CONFIG ERROR: {e}")
        raise

    restore_telegram_session()

    # Start dummy HTTP server in background thread so Render sees a web service
    thread = threading.Thread(target=start_health_server, daemon=True)
    thread.start()

    print("[scheduler] Running initial ingestion cycle...")
    run_ingestion_and_nlp()

    scheduler = BlockingScheduler()

    # ── 15-minute ingestion + NLP cycle ─────────────────────────────────────
    scheduler.add_job(
        run_ingestion_and_nlp,
        trigger=IntervalTrigger(minutes=15),
        id='ingestion_cycle',
        name='Fetch all sources + run NLP pipeline every 15 minutes',
        max_instances=1,
        coalesce=True,
    )

    # ── Daily cleanup at 02:00 UTC ───────────────────────────────────────────
    # Runs in a low-traffic window, separate from ingestion so they never overlap.
    # Retention window: ARTICLE_RETENTION_DAYS env var (default 90, min 14).
    scheduler.add_job(
        run_daily_cleanup,
        trigger=CronTrigger(hour=2, minute=0, timezone='UTC'),
        id='daily_cleanup',
        name='Delete stale articles older than ARTICLE_RETENTION_DAYS',
        max_instances=1,
        coalesce=True,
    )

    print("[scheduler] Scheduler started — ingestion every 15 min, cleanup daily at 02:00 UTC.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[scheduler] Shutting down.")


if __name__ == '__main__':
    main()

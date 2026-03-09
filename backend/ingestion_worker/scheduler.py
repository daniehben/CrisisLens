import os
import base64
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from backend.ingestion_worker.worker import run_worker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def restore_telegram_session():
    """Reconstruct Telegram session file from base64 env var on Render."""
    b64 = os.getenv('TELEGRAM_SESSION_B64')
    if not b64:
        print("[scheduler] No TELEGRAM_SESSION_B64 found — using existing session file")
        return
    session_path = 'backend/ingestion_worker/telegram.session'
    if os.path.exists(session_path):
        print("[scheduler] Session file already exists — skipping restore")
        return
    with open(session_path, 'wb') as f:
        f.write(base64.b64decode(b64))
    print("[scheduler] Telegram session file restored from env var")


def main():
    print("[scheduler] CrisisLens ingestion worker starting...")
    restore_telegram_session()

    print("[scheduler] Running initial ingestion cycle...")
    run_worker()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_worker,
        trigger=IntervalTrigger(minutes=15),
        id='ingestion_cycle',
        name='Fetch all sources every 15 minutes',
        max_instances=1,
        coalesce=True,
    )

    print("[scheduler] Scheduler started — running every 15 minutes.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[scheduler] Shutting down.")


if __name__ == '__main__':
    main()
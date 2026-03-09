from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from backend.ingestion_worker.worker import run_worker
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def main():
    print("[scheduler] CrisisLens ingestion worker starting...")

    # Run immediately on startup
    print("[scheduler] Running initial ingestion cycle...")
    run_worker()

    # Then every 15 minutes
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
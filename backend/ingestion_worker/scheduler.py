import os
import base64
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from backend.ingestion_worker.worker import run_worker
from backend.nlp_pipeline.task8_translate import run_task8
from backend.nlp_pipeline.task9_embed import run_task9
from backend.nlp_pipeline.task10_pairs import run_task10
from backend.nlp_pipeline.task11_nli import run_task11
from backend.nlp_pipeline.task12_conflicts import run_task12

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
        self.end_headers()
        self.wfile.write(b'{"status": "worker running"}')

    def log_message(self, format, *args):
        pass  # silence HTTP logs


def start_health_server():
    port = int(os.getenv('PORT', 8001))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"[scheduler] Health server listening on port {port}")
    server.serve_forever()

def run_ingestion_and_nlp():
    run_worker()
    run_task8()
    run_task9()
    run_task10()
    run_task11()
    run_task12()
    
    
def main():
    print("[scheduler] CrisisLens ingestion worker starting...")
    restore_telegram_session()

    # Start dummy HTTP server in background thread so Render sees a web service
    thread = threading.Thread(target=start_health_server, daemon=True)
    thread.start()

    print("[scheduler] Running initial ingestion cycle...")
    run_ingestion_and_nlp()
    

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_ingestion_and_nlp,
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

"""Task 15 — Stale article cleanup.

Why this exists
---------------
The ingestion worker inserts articles continuously but never deletes them.
Over time this causes three problems:

  1. Storage exhaustion — Supabase free tier is 500MB. At ~200 articles/day,
     fully processed articles average ~6KB each (headline, body, summary,
     embedding vector). That's ~36MB/month → ~14 months to hit 500MB under
     normal load. A major news event can spike 500+ articles in a single day,
     cutting that window to 6 months or less. When Supabase hits 500MB the DB
     goes READ-ONLY — the pipeline silently stops ingesting with no user-visible
     error.

  2. NLP pipeline slowdown — Task9/10/11 query articles with WHERE embedding IS NULL
     or WHERE status = 'pending'. As the table grows these scans slow down even
     with indexes. Old processed rows add no value but still cost query time.

  3. Contradiction pair explosion — Task10 compares new articles against the full
     article pool. Old articles generate stale pairs (contradictions between
     6-month-old reports nobody will read) that pollute task11's NLI queue.

Retention window
----------------
Controlled by the ARTICLE_RETENTION_DAYS environment variable (default: 90).

  Tighten (shorter window) when:
    - Supabase storage is approaching 400MB (check Supabase dashboard)
    - You want to reduce NLP pair volume
    - Recommended minimum: 30 days

  Loosen (longer window) when:
    - You upgrade to Supabase Pro (500GB, no expiry pressure)
    - You want longer historical contradiction data
    - You have researchers or analysts who need older articles
    - Recommended maximum on free tier: 120 days

  To change: set ARTICLE_RETENTION_DAYS in Render dashboard env vars.
  No code change or redeployment needed — value is read every cleanup run.

What gets deleted
-----------------
  1. articles older than ARTICLE_RETENTION_DAYS
  2. article_pairs that reference deleted articles (CASCADE or explicit delete)
  3. conflicts that reference deleted pairs (CASCADE or explicit delete)

The embedding vector column is the largest per-row cost (~1.5KB per article).
Deleting old rows reclaims that storage immediately in Supabase's free tier.

Schedule
--------
Runs once daily (not on the 15-minute ingestion cycle) to avoid competing
with live ingestion. Wired into the scheduler as a separate APScheduler job
at 02:00 UTC — low-traffic window.
"""
import logging
import os
from datetime import datetime, timezone

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

# Default retention: 90 days. Override with ARTICLE_RETENTION_DAYS env var.
DEFAULT_RETENTION_DAYS = 90

# Hard floor — never delete articles newer than this regardless of env var.
# Prevents accidental misconfiguration from wiping recent articles.
MIN_RETENTION_DAYS = 14


def _get_retention_days() -> int:
    """
    Read ARTICLE_RETENTION_DAYS from environment. Applies bounds:
      - Below MIN_RETENTION_DAYS (14): clamped up, warning logged.
      - Non-integer or missing: falls back to DEFAULT_RETENTION_DAYS (90).
    """
    raw = os.getenv("ARTICLE_RETENTION_DAYS", "")
    if not raw.strip():
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError:
        log.warning(
            f"[Task15] ARTICLE_RETENTION_DAYS='{raw}' is not a valid integer "
            f"— using default {DEFAULT_RETENTION_DAYS} days"
        )
        return DEFAULT_RETENTION_DAYS

    if days < MIN_RETENTION_DAYS:
        log.warning(
            f"[Task15] ARTICLE_RETENTION_DAYS={days} is below the minimum "
            f"of {MIN_RETENTION_DAYS} days — clamping to {MIN_RETENTION_DAYS} "
            f"to prevent accidental data loss"
        )
        return MIN_RETENTION_DAYS

    return days


def run_task15() -> dict:
    """
    Delete articles older than ARTICLE_RETENTION_DAYS and cascade-clean
    dependent rows (article_pairs, conflicts).

    Returns a summary dict:
      {
        "retention_days": int,
        "cutoff_date":    str (ISO),
        "articles_deleted": int,
        "pairs_deleted":    int,
        "conflicts_deleted": int,
        "storage_note":     str,
      }
    """
    retention_days = _get_retention_days()
    log.info(
        f"[Task15] Starting cleanup — retention window: {retention_days} days "
        f"(ARTICLE_RETENTION_DAYS={os.getenv('ARTICLE_RETENTION_DAYS', 'not set, using default')})"
    )

    articles_deleted = 0
    pairs_deleted    = 0
    conflicts_deleted = 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:

                # ── Step 1: count what we're about to delete (for logging) ──
                cur.execute("""
                    SELECT COUNT(*)
                    FROM articles
                    WHERE fetched_at < NOW() - INTERVAL '%s days'
                """, (retention_days,))
                articles_to_delete = cur.fetchone()[0]

                if articles_to_delete == 0:
                    log.info("[Task15] No stale articles found — nothing to clean up.")
                    return {
                        "retention_days": retention_days,
                        "cutoff_date": None,
                        "articles_deleted": 0,
                        "pairs_deleted": 0,
                        "conflicts_deleted": 0,
                        "storage_note": "No action taken.",
                    }

                # ── Step 2: delete stale conflicts first (reference pairs) ──
                cur.execute("""
                    DELETE FROM conflicts
                    WHERE pair_id IN (
                        SELECT ap.pair_id
                        FROM article_pairs ap
                        JOIN articles a ON (a.article_id = ap.article_id_1
                                         OR a.article_id = ap.article_id_2)
                        WHERE a.fetched_at < NOW() - INTERVAL '%s days'
                    )
                """, (retention_days,))
                conflicts_deleted = cur.rowcount

                # ── Step 3: delete stale article_pairs ──
                cur.execute("""
                    DELETE FROM article_pairs
                    WHERE article_id_1 IN (
                        SELECT article_id FROM articles
                        WHERE fetched_at < NOW() - INTERVAL '%s days'
                    )
                    OR article_id_2 IN (
                        SELECT article_id FROM articles
                        WHERE fetched_at < NOW() - INTERVAL '%s days'
                    )
                """, (retention_days, retention_days))
                pairs_deleted = cur.rowcount

                # ── Step 4: delete stale articles ──
                cur.execute("""
                    DELETE FROM articles
                    WHERE fetched_at < NOW() - INTERVAL '%s days'
                    RETURNING article_id
                """, (retention_days,))
                articles_deleted = cur.rowcount

            conn.commit()

        # Approximate storage reclaimed (rough: 6KB per fully-processed article)
        kb_reclaimed = articles_deleted * 6
        storage_note = (
            f"~{kb_reclaimed:,}KB (~{kb_reclaimed // 1024}MB) estimated reclaimed. "
            f"Check Supabase dashboard for actual storage."
        )

        log.info(
            f"[Task15] Cleanup complete — "
            f"deleted {articles_deleted:,} articles, "
            f"{pairs_deleted:,} pairs, "
            f"{conflicts_deleted:,} conflicts. "
            f"{storage_note}"
        )

        # Log a storage advisory if the cleanup was large
        if articles_deleted > 500:
            log.warning(
                f"[Task15] Large cleanup ({articles_deleted:,} articles deleted). "
                f"Consider tightening ARTICLE_RETENTION_DAYS if this happens regularly. "
                f"Current window: {retention_days} days."
            )

        cutoff = datetime.now(timezone.utc)
        return {
            "retention_days":    retention_days,
            "cutoff_date":       cutoff.isoformat(),
            "articles_deleted":  articles_deleted,
            "pairs_deleted":     pairs_deleted,
            "conflicts_deleted": conflicts_deleted,
            "storage_note":      storage_note,
        }

    except Exception as e:
        log.error(f"[Task15] Cleanup failed: {e}", exc_info=True)
        return {
            "retention_days":    retention_days,
            "cutoff_date":       None,
            "articles_deleted":  0,
            "pairs_deleted":     0,
            "conflicts_deleted": 0,
            "storage_note":      f"ERROR: {e}",
        }

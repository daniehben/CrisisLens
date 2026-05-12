"""Task 7.5 — LLM-clean and summarize each article body.

Input: messy body_snippet from task7 (trafilatura output).
Output: 2-3 clean factual sentences in articles.summary.

Runs after task7. Skipped silently if GROQ_API_KEY isn't set.
"""
import logging

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat, FAST_MODEL

log = logging.getLogger(__name__)

# Lowered to spread Groq RPM and keep memory bounded.
BATCH_SIZE = 15

PROMPT = """You're a news summarization assistant. Summarize the article below \
in 2-3 short, factual sentences. Be strictly neutral — no editorializing. \
Match the language of the input (Arabic stays Arabic, English stays English). \
Output ONLY the summary text, no preamble or quotes.

Article:
{body}"""


def run_task7_5():
    log.info("[Task7.5] Starting LLM body summarization...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, body_snippet
                FROM articles
                WHERE summary IS NULL
                  AND body_snippet IS NOT NULL
                  AND LENGTH(body_snippet) >= 200
                ORDER BY article_id DESC
                LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task7.5] Nothing to summarize.")
        return 0

    log.info(f"[Task7.5] Summarizing {len(rows)} articles...")

    written = 0
    failed = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, body in rows:
                summary = chat(PROMPT.format(body=body[:2500]), model=FAST_MODEL,
                               max_tokens=200)
                if not summary or len(summary.strip()) < 30:
                    failed += 1
                    continue
                cur.execute(
                    "UPDATE articles SET summary = %s WHERE article_id = %s",
                    (summary.strip(), article_id),
                )
                written += 1
            conn.commit()

    log.info(f"[Task7.5] Complete — {written} summarized, {failed} failed")
    return written

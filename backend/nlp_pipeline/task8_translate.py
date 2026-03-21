import gc
import logging
from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException
from deep_translator import GoogleTranslator
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)


def strip_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()


def detect_language(text: str, fallback: str = "en") -> str:
    try:
        return detect(text)
    except LangDetectException:
        return fallback


def translate_to_arabic(texts: list[str]) -> list[str]:
    translator = GoogleTranslator(source='en', target='ar')
    results = []
    for text in texts:
        try:
            results.append(translator.translate(text))
        except Exception as e:
            log.warning(f"[Task8] Translation failed: {e}")
            results.append(text)
    return results


def run_task8():
    log.info("[Task8] Starting language detection + translation...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, language, headline_en, headline_ar, body_snippet
                FROM articles
                WHERE processed_nlp = FALSE
                  AND (headline_en IS NOT NULL OR headline_ar IS NOT NULL)
                ORDER BY article_id
                LIMIT 200
            """)
            rows = cur.fetchall()

    if not rows:
        log.info("[Task8] No unprocessed articles found.")
        return 0

    log.info(f"[Task8] Processing {len(rows)} articles...")

    to_translate = []
    already_arabic = []

    for article_id, language, headline_en, headline_ar, body_snippet in rows:
        clean_snippet = strip_html(body_snippet) if body_snippet else None
        if headline_ar:
            already_arabic.append((article_id, headline_ar, clean_snippet))
        elif headline_en:
            to_translate.append((article_id, headline_en, clean_snippet))

    if already_arabic:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for article_id, headline_ar, clean_snippet in already_arabic:
                    cur.execute("""
                        UPDATE articles
                        SET body_snippet = %s
                        WHERE article_id = %s
                    """, (clean_snippet, article_id))
            conn.commit()
        log.info(f"[Task8] Cleaned body_snippet for {len(already_arabic)} Arabic articles")

    if to_translate:
        log.info(f"[Task8] Translating {len(to_translate)} English headlines to Arabic...")
        batch_size = 16
        translated_count = 0

        for i in range(0, len(to_translate), batch_size):
            batch = to_translate[i:i + batch_size]
            article_ids = [b[0] for b in batch]
            headlines = [b[1] for b in batch]
            snippets = [b[2] for b in batch]

            translated = translate_to_arabic(headlines)

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    for j, (article_id, translated_headline) in enumerate(zip(article_ids, translated)):
                        cur.execute("""
                            UPDATE articles
                            SET headline_ar = %s,
                                headline_ar_translated = TRUE,
                                body_snippet = %s
                            WHERE article_id = %s
                        """, (translated_headline, snippets[j], article_id))
                conn.commit()

            translated_count += len(batch)
            log.info(f"[Task8] Translated {translated_count}/{len(to_translate)}")

    total = len(already_arabic) + len(to_translate)
    log.info(f"[Task8] Complete — {total} articles processed ({len(to_translate)} translated, {len(already_arabic)} Arabic)")
    return total
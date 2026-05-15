import logging
from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException
from deep_translator import GoogleTranslator
from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat, FAST_MODEL

log = logging.getLogger(__name__)

_TRANSLATE_PROMPT = """\
Translate the following news text to natural Modern Standard Arabic (MSA) \
as used in professional Arabic news reporting. \
Output ONLY the Arabic translation — no preamble, no explanation, no quotes.

{text}"""


def strip_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()


def detect_language(text: str, fallback: str = "en") -> str:
    try:
        return detect(text)
    except LangDetectException:
        return fallback


def translate_to_arabic_groq(text: str) -> str | None:
    """Translate a single text to Arabic using Groq (natural MSA).
    Returns None on failure so the caller can fall back."""
    if not text or not text.strip():
        return text
    result = chat(_TRANSLATE_PROMPT.format(text=text.strip()),
                  model=FAST_MODEL, max_tokens=400)
    if result and len(result.strip()) > 3:
        return result.strip()
    return None


def translate_to_arabic_google(texts: list[str]) -> list[str]:
    """Batch-translate via Google Translate — fallback only."""
    translator = GoogleTranslator(source='en', target='ar')
    try:
        translated = translator.translate_batch(texts)
        return [t if t else original for t, original in zip(translated, texts)]
    except Exception as e:
        log.warning(f"[Task8] Google batch failed, falling back per-text: {e}")
    results = []
    for text in texts:
        try:
            results.append(translator.translate(text) or text)
        except Exception:
            results.append(text)
    return results


def translate_to_arabic(texts: list[str], source: str = 'en') -> list[str]:
    """Translate a list of texts to Arabic.
    Tries Groq first for natural MSA quality; falls back to Google Translate
    if Groq is unavailable or returns an empty result."""
    results = []
    google_fallback_indices = []
    google_fallback_texts = []

    for i, text in enumerate(texts):
        ar = translate_to_arabic_groq(text)
        if ar:
            results.append(ar)
        else:
            # Queue for Google batch fallback
            results.append(None)
            google_fallback_indices.append(i)
            google_fallback_texts.append(text)

    if google_fallback_texts:
        log.info(f"[Task8] Falling back to Google Translate for {len(google_fallback_texts)} texts")
        google_results = translate_to_arabic_google(google_fallback_texts)
        for idx, translated in zip(google_fallback_indices, google_results):
            results[idx] = translated

    return [r if r is not None else texts[i] for i, r in enumerate(results)]


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
            headlines   = [b[1] for b in batch]
            snippets    = [b[2] for b in batch]

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

    # Also translate any pending summaries
    run_task8_summaries()

    return total


def run_task8_summaries():
    """Translate English article summaries to natural Arabic via Groq.

    Runs a separate query (not tied to processed_nlp) so it also catches
    articles whose summaries were generated after their initial task8 pass.
    One Groq call per summary — slower than batch but produces natural MSA.
    """
    log.info("[Task8-Summaries] Translating English summaries to Arabic...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, summary
                FROM articles
                WHERE summary IS NOT NULL
                  AND summary_ar IS NULL
                  AND language = 'en'
                ORDER BY article_id DESC
                LIMIT 60
            """)
            rows = cur.fetchall()

    if not rows:
        log.info("[Task8-Summaries] No summaries to translate.")
        return 0

    log.info(f"[Task8-Summaries] Translating {len(rows)} summaries...")
    translated_count = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, summary in rows:
                ar = translate_to_arabic_groq(summary)
                if not ar:
                    # Groq unavailable — fall back to Google for this one
                    try:
                        ar = GoogleTranslator(source='en', target='ar').translate(summary)
                    except Exception:
                        ar = None
                if ar:
                    cur.execute(
                        "UPDATE articles SET summary_ar = %s WHERE article_id = %s",
                        (ar, article_id),
                    )
                    translated_count += 1
            conn.commit()

    log.info(f"[Task8-Summaries] Complete — {translated_count}/{len(rows)} summaries translated")
    return translated_count

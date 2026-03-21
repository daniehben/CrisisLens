import gc
import logging
from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()


def detect_language(text: str, fallback: str = "en") -> str:
    """Detect language of text, return fallback on failure."""
    try:
        return detect(text)
    except LangDetectException:
        return fallback


def translate_batch(texts: list[str], model, tokenizer) -> list[str]:
    """Translate a batch of English texts to Arabic."""
    results = []
    for text in texts:
        try:
            inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
            outputs = model.generate(**inputs, max_length=512)
            translated = tokenizer.decode(outputs[0], skip_special_tokens=True)
            results.append(translated)
        except Exception as e:
            log.warning(f"Translation failed for text: {e}")
            results.append(text)  # keep original on failure
    return results


def run_task8():
    """
    Task 8: Language detection + translation pipeline.
    - Strip HTML from body_snippet
    - Detect language of headline
    - Translate English headlines to Arabic
    - Populate headline_ar for all articles
    - Mark processed_nlp=false (will be set true after Task 9)
    """
    log.info("[Task8] Starting language detection + translation...")

    # fetch unprocessed articles
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

    # separate into needs-translation vs already-arabic
    to_translate = []  # (article_id, headline_en)
    already_arabic = []  # (article_id, headline_ar, body_snippet)

    for article_id, language, headline_en, headline_ar, body_snippet in rows:
        # strip HTML from body_snippet
        clean_snippet = strip_html(body_snippet) if body_snippet else None

        if headline_ar:
            # already has Arabic — clean snippet only
            already_arabic.append((article_id, headline_ar, clean_snippet))
        elif headline_en:
            # needs translation
            to_translate.append((article_id, headline_en, clean_snippet))

    # update already-arabic articles (just clean body_snippet)
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

    # translate English headlines
    if to_translate:
        log.info(f"[Task8] Translating {len(to_translate)} English headlines to Arabic...")
        try:
            from transformers import MarianMTModel, MarianTokenizer
            model_name = "Helsinki-NLP/opus-mt-en-ar"
            tokenizer = MarianTokenizer.from_pretrained(model_name)
            model = MarianMTModel.from_pretrained(model_name)
            model.eval()

            batch_size = 16
            translated_count = 0

            for i in range(0, len(to_translate), batch_size):
                batch = to_translate[i:i + batch_size]
                article_ids = [b[0] for b in batch]
                headlines = [b[1] for b in batch]
                snippets = [b[2] for b in batch]

                translated = translate_batch(headlines, model, tokenizer)

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

            # free model memory
            del model, tokenizer
            gc.collect()
            log.info("[Task8] Translation model unloaded from memory")

        except Exception as e:
            log.error(f"[Task8] Translation pipeline failed: {e}")
            return 0

    total = len(already_arabic) + len(to_translate)
    log.info(f"[Task8] Complete — processed {total} articles ({len(to_translate)} translated, {len(already_arabic)} already Arabic)")
    return total
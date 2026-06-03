"""Task 9 — Generate sentence embeddings for article headlines.

Model: paraphrase-multilingual-MiniLM-L12-v2 (local, CPU inference)
  - 384-dim vectors, multilingual (Arabic + English in same space)
  - ~120MB download on first run, cached at ~/.cache/huggingface/
  - ~50-150ms per sentence on CPU — acceptable for background worker
  - Zero API quota, zero cost, no external dependency

Why local over HF Inference API:
  - HF free tier: ~100 req/day hard cap, silently stalls on busy days
  - Local: unlimited, faster (no network), same model quality
  - Render free tier has 512MB RAM; model loads in ~200MB

Why this model:
  - Handles Arabic and English in the same vector space
  - Enables cross-lingual contradiction detection (AP English vs AJA+ Arabic
    about the same event will land close in embedding space)
  - Lightweight enough for Render free tier CPU inference

Future upgrade path: OpenAI text-embedding-3-small at $0.02/1M tokens
  (~$0.004/month at current scale). See docs/BUDGET.md.
"""
import logging
import os

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Module-level cache — model loads once per worker process, not per cycle
_model = None


def _get_model():
    """Lazy-load the sentence-transformers model. Cached after first call."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            log.info(f"[Task9] Loading embedding model {MODEL_NAME} (first run may download ~120MB)...")
            _model = SentenceTransformer(MODEL_NAME)
            log.info("[Task9] Model loaded.")
        except ImportError:
            log.error("[Task9] sentence-transformers not installed — run: pip install sentence-transformers")
            return None
        except Exception as e:
            log.error(f"[Task9] Failed to load model: {e}")
            return None
    return _model


def get_embeddings_local(texts: list[str], batch_size: int = 32) -> list[list[float] | None]:
    """Embed a list of texts locally. Returns one 384-dim vector per input, or None on failure."""
    model = _get_model()
    if model is None:
        return [None] * len(texts)

    results: list[list[float] | None] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            vecs = model.encode(batch, show_progress_bar=False, convert_to_numpy=True)
            for vec in vecs:
                results.append(vec.tolist())
        except Exception as e:
            log.warning(f"[Task9] Batch {i}–{i+len(batch)} failed: {e} — appending Nones")
            results.extend([None] * len(batch))

    return results


def run_task9():
    log.info("[Task9] Starting embedding generation...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Embed whichever headline is available: prefer Arabic, fall back to English.
            # This ensures English-only sources (AP, BBC, Reuters…) also get embeddings
            # and can participate in cross-lingual contradiction detection.
            cur.execute("""
                SELECT article_id,
                       COALESCE(headline_ar, headline_en) AS headline
                FROM articles
                WHERE (headline_ar IS NOT NULL OR headline_en IS NOT NULL)
                  AND embedding IS NULL
                ORDER BY article_id
                LIMIT 100
            """)
            rows = cur.fetchall()

    if not rows:
        log.info("[Task9] No articles need embedding.")
        return 0

    log.info(f"[Task9] Generating embeddings for {len(rows)} articles...")

    article_ids = [r[0] for r in rows]
    headlines   = [r[1] for r in rows]

    embeddings = get_embeddings_local(headlines)

    stored = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, embedding in zip(article_ids, embeddings):
                if embedding is None:
                    continue
                cur.execute("""
                    UPDATE articles
                    SET embedding = %s::vector,
                        processed_nlp = TRUE
                    WHERE article_id = %s
                """, (str(embedding), article_id))
                stored += 1
        conn.commit()

    log.info(f"[Task9] Complete — {stored}/{len(rows)} embeddings stored")
    return stored

import gc
import logging
import json
import httpx
import os
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

HF_API_URL = "https://router.huggingface.co/hf-inference/models/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/pipeline/feature-extraction"



def _normalize_embedding(raw) -> list[float] | None:
    """HF returns either a sentence vector [floats] or token vectors [[floats]].
    Mean-pool token vectors so we always return one vector per input."""
    if not raw:
        return None
    if isinstance(raw[0], list):
        import numpy as np
        return np.mean(raw, axis=0).tolist()
    return raw


def get_embeddings_from_api(texts: list[str], hf_token: str, batch_size: int = 16) -> list[list[float] | None]:
    """Batch embed texts via HF Inference API.
    Sends batch_size texts per request instead of one. Falls back to per-text
    on batch error so a single malformed input doesn't drop the whole batch."""
    headers = {"Authorization": f"Bearer {hf_token}"}
    results: list[list[float] | None] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            response = httpx.post(
                HF_API_URL,
                headers=headers,
                json={"inputs": batch},
                timeout=60,
            )
            if response.status_code == 200:
                payload = response.json()
                # Batch response: list of embeddings, one per input
                for raw in payload:
                    results.append(_normalize_embedding(raw))
                continue
            log.warning(f"[Task9] HF batch error {response.status_code}: {response.text[:120]}")
        except Exception as e:
            log.warning(f"[Task9] HF batch request failed: {e}")

        # Batch failed — fall back to one-at-a-time for this batch only
        for text in batch:
            try:
                response = httpx.post(
                    HF_API_URL,
                    headers=headers,
                    json={"inputs": text},
                    timeout=30,
                )
                if response.status_code == 200:
                    results.append(_normalize_embedding(response.json()))
                else:
                    results.append(None)
            except Exception as e:
                log.warning(f"[Task9] Per-text fallback failed: {e}")
                results.append(None)

    return results


def run_task9():
    log.info("[Task9] Starting embedding generation...")

    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        log.error("[Task9] HF_TOKEN env var not set — skipping")
        return 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, headline_ar
                FROM articles
                WHERE headline_ar IS NOT NULL
                  AND processed_nlp = FALSE
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
    headlines = [r[1] for r in rows]

    embeddings = get_embeddings_from_api(headlines, hf_token)

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
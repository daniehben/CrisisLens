import gc
import logging
import json
import httpx
import os
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

HF_API_URL = "https://api-inference.huggingface.co/models/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def get_embeddings_from_api(texts: list[str], hf_token: str) -> list[list[float]]:
    headers = {"Authorization": f"Bearer {hf_token}"}
    results = []
    for text in texts:
        try:
            response = httpx.post(
                HF_API_URL,
                headers=headers,
                json={"inputs": text},
                timeout=30
            )
            if response.status_code == 200:
                embedding = response.json()
                if isinstance(embedding[0], list):
                    # model returned token embeddings, mean pool
                    import numpy as np
                    embedding = np.mean(embedding, axis=0).tolist()
                results.append(embedding)
            else:
                log.warning(f"[Task9] HF API error {response.status_code}: {response.text[:100]}")
                results.append(None)
        except Exception as e:
            log.warning(f"[Task9] Embedding request failed: {e}")
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
import gc
import logging
import numpy as np
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)


def run_task9():
    log.info("[Task9] Starting embedding generation...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT article_id, headline_ar
                FROM articles
                WHERE headline_ar IS NOT NULL
                  AND processed_nlp = FALSE
                  AND embedding IS NULL
                ORDER BY article_id
                LIMIT 200
            """)
            rows = cur.fetchall()

    if not rows:
        log.info("[Task9] No articles need embedding.")
        return 0

    log.info(f"[Task9] Generating embeddings for {len(rows)} articles...")

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

        article_ids = [r[0] for r in rows]
        headlines = [r[1] for r in rows]

        batch_size = 32
        all_embeddings = []

        for i in range(0, len(headlines), batch_size):
            batch = headlines[i:i + batch_size]
            embeddings = model.encode(batch, show_progress_bar=False)
            all_embeddings.extend(embeddings)
            log.info(f"[Task9] Embedded {min(i + batch_size, len(headlines))}/{len(headlines)}")

        del model
        gc.collect()

        # store embeddings in DB
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for article_id, embedding in zip(article_ids, all_embeddings):
                    embedding_list = embedding.tolist()
                    cur.execute("""
                        UPDATE articles
                        SET embedding = %s::vector,
                            processed_nlp = TRUE
                        WHERE article_id = %s
                    """, (str(embedding_list), article_id))
            conn.commit()

        log.info(f"[Task9] Complete — {len(rows)} embeddings stored")
        return len(rows)

    except Exception as e:
        log.error(f"[Task9] Embedding pipeline failed: {e}")
        return 0

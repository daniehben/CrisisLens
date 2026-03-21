import logging
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75
TIME_WINDOW_HOURS = 48
TOP_K = 10


def run_task10():
    log.info("[Task10] Starting candidate pair generation...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # find articles with embeddings not yet paired
            cur.execute("""
                SELECT a.article_id, a.source_id
                FROM articles a
                WHERE a.embedding IS NOT NULL
                  AND a.processed_nlp = TRUE
                  AND a.article_id NOT IN (
                      SELECT article_id_1 FROM article_pairs
                      UNION
                      SELECT article_id_2 FROM article_pairs
                  )
                ORDER BY a.article_id DESC
                LIMIT 50
            """)
            new_articles = cur.fetchall()

    if not new_articles:
        log.info("[Task10] No new articles to pair.")
        return 0

    log.info(f"[Task10] Finding pairs for {len(new_articles)} articles...")

    pairs_inserted = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, source_id in new_articles:
                # find top-K similar articles from DIFFERENT sources
                cur.execute("""
                    SELECT
                        a2.article_id,
                        1 - (a1.embedding <=> a2.embedding) AS similarity
                    FROM articles a1
                    JOIN articles a2 ON a2.article_id != a1.article_id
                    WHERE a1.article_id = %s
                      AND a2.embedding IS NOT NULL
                      AND a2.source_id != a1.source_id
                      AND a2.published_at BETWEEN
                          a1.published_at - INTERVAL '%s hours'
                          AND a1.published_at + INTERVAL '%s hours'
                      AND 1 - (a1.embedding <=> a2.embedding) >= %s
                    ORDER BY a1.embedding <=> a2.embedding
                    LIMIT %s
                """, (article_id, TIME_WINDOW_HOURS, TIME_WINDOW_HOURS,
                      SIMILARITY_THRESHOLD, TOP_K))

                similar = cur.fetchall()

                for similar_id, similarity in similar:
                    # ensure consistent ordering to avoid duplicates
                    id1 = min(article_id, similar_id)
                    id2 = max(article_id, similar_id)

                    cur.execute("""
                        INSERT INTO article_pairs
                            (article_id_1, article_id_2, similarity_score)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (article_id_1, article_id_2) DO NOTHING
                    """, (id1, id2, similarity))

                    if cur.rowcount == 1:
                        pairs_inserted += 1

        conn.commit()

    log.info(f"[Task10] Complete — {pairs_inserted} candidate pairs inserted")
    return pairs_inserted
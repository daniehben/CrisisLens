import logging
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

CONTRADICTION_THRESHOLD = 0.3
CONFLICT_SCORE_THRESHOLD = 0.1


def run_task12():
    log.info("[Task12] Starting conflict scoring...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ap.pair_id,
                    ap.article_id_1,
                    ap.article_id_2,
                    ap.similarity_score,
                    ap.contradiction_score,
                    a1.trust_weight AS trust_1,
                    a2.trust_weight AS trust_2
                FROM article_pairs ap
                JOIN articles a1 ON a1.article_id = ap.article_id_1
                JOIN articles a2 ON a2.article_id = ap.article_id_2
                WHERE ap.status = 'processed'
                  AND ap.nli_label = 'contradiction'
                  AND ap.contradiction_score >= %s
                  AND ap.pair_id NOT IN (
                    SELECT article_a_id FROM conflicts
                    UNION
                    SELECT article_b_id FROM conflicts
                )
            """, (CONTRADICTION_THRESHOLD,))
            pairs = cur.fetchall()

    if not pairs:
        log.info("[Task12] No contradiction pairs to score.")
        return 0

    log.info(f"[Task12] Scoring {len(pairs)} contradiction pairs...")

    conflicts_inserted = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for pair_id, id1, id2, similarity, contradiction_score, trust_1, trust_2 in pairs:
                trust_1 = trust_1 or 0.5
                trust_2 = trust_2 or 0.5

                # conflict score: NLI confidence × trust differential × max trust
                trust_diff = abs(trust_1 - trust_2)
                max_trust = max(trust_1, trust_2)
                conflict_score = contradiction_score * trust_diff * max_trust

                if conflict_score < CONFLICT_SCORE_THRESHOLD:
                    continue

                cur.execute("""
                    INSERT INTO conflicts (
                        article_a_id, article_b_id,
                        conflict_type,
                        similarity_score, nli_label, nli_confidence, weighted_score
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (article_a_id, article_b_id) DO NOTHING
                """, (id1, id2, 'contradiction',
                    round(similarity, 4),
                    'contradiction',
                    round(contradiction_score, 4),
                    round(conflict_score, 4)))

                if cur.rowcount == 1:
                    conflicts_inserted += 1
                    log.info(f"[Task12] Conflict stored — score={conflict_score:.3f} "
                             f"(similarity={similarity:.3f}, contradiction={contradiction_score:.3f})")

        conn.commit()

    log.info(f"[Task12] Complete — {conflicts_inserted} conflicts stored")
    return conflicts_inserted
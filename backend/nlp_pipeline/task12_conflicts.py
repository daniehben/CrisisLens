import logging
from backend.shared.database import get_db_connection
from backend.nlp_pipeline.heuristics import numeric_disagreement, is_same_story

log = logging.getLogger(__name__)

CONTRADICTION_THRESHOLD = 0.55  # raised from 0.3 — kills noisy borderline pairs
CONFLICT_SCORE_THRESHOLD = 0.30  # raised proportionally: 0.55 × min trust 0.5 = 0.275
NUMERIC_BOOST = 0.20             # added to conflict_score when numbers disagree


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
                    a2.trust_weight AS trust_2,
                    a1.headline_en AS h1_en, a1.headline_ar AS h1_ar,
                    a2.headline_en AS h2_en, a2.headline_ar AS h2_ar
                FROM article_pairs ap
                JOIN articles a1 ON a1.article_id = ap.article_id_1
                JOIN articles a2 ON a2.article_id = ap.article_id_2
                WHERE ap.status = 'processed'
                  AND ap.nli_label = 'contradiction'
                  AND ap.contradiction_score >= %s
                  AND NOT EXISTS (
                      SELECT 1 FROM conflicts c
                      WHERE (c.article_a_id = ap.article_id_1 AND c.article_b_id = ap.article_id_2)
                         OR (c.article_a_id = ap.article_id_2 AND c.article_b_id = ap.article_id_1)
                  )
            """, (CONTRADICTION_THRESHOLD,))
            pairs = cur.fetchall()

    if not pairs:
        log.info("[Task12] No contradiction pairs to score.")
        return 0

    log.info(f"[Task12] Scoring {len(pairs)} contradiction pairs...")

    conflicts_inserted = 0
    same_story_skipped = 0
    numeric_boosted = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for row in pairs:
                (pair_id, id1, id2, similarity, contradiction_score,
                 trust_1, trust_2, h1_en, h1_ar, h2_en, h2_ar) = row

                trust_1 = float(trust_1 or 0.5)
                trust_2 = float(trust_2 or 0.5)
                similarity = float(similarity)
                contradiction_score = float(contradiction_score)

                a_texts = [h1_en, h1_ar]
                b_texts = [h2_en, h2_ar]

                # Heuristic 1 — same-story suppression. If the pair looks like
                # two outlets reporting the SAME event with different framing
                # (high similarity + keyword overlap + no numeric disagreement),
                # skip. This is the #1 false-positive pattern in our labeled set.
                if is_same_story(similarity, a_texts, b_texts):
                    same_story_skipped += 1
                    log.info(f"[Task12] Pair {pair_id} skipped as same-story "
                             f"(similarity={similarity:.3f})")
                    continue

                # Conflict score: contradiction × max trust (rewards trustable sources)
                max_trust = max(trust_1, trust_2)
                conflict_score = contradiction_score * max_trust

                # Heuristic 2 — numeric disagreement boost. A real "10 vs 7
                # casualties" type pair gets pushed above threshold even if
                # NLI confidence is moderate.
                numeric = numeric_disagreement(a_texts, b_texts)
                if numeric:
                    conflict_score = min(conflict_score + NUMERIC_BOOST, 1.0)
                    numeric_boosted += 1

                if conflict_score < CONFLICT_SCORE_THRESHOLD:
                    continue

                cur.execute("""
                    INSERT INTO conflicts (
                        article_a_id, article_b_id,
                        conflict_type,
                        similarity_score, nli_label, nli_confidence, weighted_score
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (article_a_id, article_b_id) DO NOTHING
                """, (id1, id2,
                    'numeric' if numeric else 'contradiction',
                    round(similarity, 4),
                    'contradiction',
                    round(contradiction_score, 4),
                    round(conflict_score, 4)))

                if cur.rowcount == 1:
                    conflicts_inserted += 1
                    tag = ' [numeric]' if numeric else ''
                    log.info(f"[Task12] Conflict stored{tag} — score={conflict_score:.3f} "
                             f"(similarity={similarity:.3f}, contradiction={contradiction_score:.3f})")

        conn.commit()

    log.info(f"[Task12] Complete — {conflicts_inserted} stored, "
             f"{same_story_skipped} same-story skipped, "
             f"{numeric_boosted} numeric-boosted")
    return conflicts_inserted
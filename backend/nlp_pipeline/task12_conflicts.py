import logging
from backend.shared.database import get_db_connection
from backend.nlp_pipeline.heuristics import (
    numeric_disagreement, is_same_story, framing_flip, is_developing_story_update
)

log = logging.getLogger(__name__)

CONTRADICTION_THRESHOLD = 0.55  # raised from 0.3 — kills noisy borderline pairs
CONFLICT_SCORE_THRESHOLD = 0.30  # raised proportionally: 0.55 × min trust 0.5 = 0.275
NUMERIC_BOOST  = 0.20            # added when numbers disagree
FRAMING_BOOST  = 0.15            # added when opposing framing vocabulary detected
DIVERSITY_BONUS = 0.08           # added when sources are from different perspective regions

# Perspective regions for diversity bonus. A pair crossing region boundaries
# is more interesting than two sources from the same region.
_REGION_GROUP = {
    'AJA': 'arab', 'ANA': 'arab', 'ARB': 'arab', 'AKH': 'arab',
    'DW': 'western', 'F24': 'western', 'BBC': 'western',
    'AP': 'western', 'WP': 'western', 'JRP': 'western',
    'TAS': 'state', 'PTV': 'state', 'RTA': 'state',
    'MND': 'pal', 'WAF': 'pal', 'EI': 'pal',
    'GG': 'indie', 'GZ': 'indie', 'CJ': 'indie',
    'AW': 'indie', 'CRA': 'indie', 'DSN': 'indie',
    'YT_BP': 'indie', 'YT_DN': 'indie', 'YT_RT': 'indie',
}


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
                    a2.headline_en AS h2_en, a2.headline_ar AS h2_ar,
                    a1.published_at AS pub1, a2.published_at AS pub2,
                    s1.code AS src1, s2.code AS src2
                FROM article_pairs ap
                JOIN articles a1 ON a1.article_id = ap.article_id_1
                JOIN articles a2 ON a2.article_id = ap.article_id_2
                JOIN sources s1 ON s1.source_id = a1.source_id
                JOIN sources s2 ON s2.source_id = a2.source_id
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
    framing_boosted = 0
    diversity_boosted = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for row in pairs:
                (pair_id, id1, id2, similarity, contradiction_score,
                 trust_1, trust_2, h1_en, h1_ar, h2_en, h2_ar,
                 pub1, pub2, src1, src2) = row

                trust_1 = float(trust_1 or 0.5)
                trust_2 = float(trust_2 or 0.5)
                similarity = float(similarity)
                contradiction_score = float(contradiction_score)

                a_texts = [h1_en, h1_ar]
                b_texts = [h2_en, h2_ar]

                # Heuristic 1 — same-story suppression.
                if is_same_story(similarity, a_texts, b_texts):
                    same_story_skipped += 1
                    log.info(f"[Task12] Pair {pair_id} skipped as same-story "
                             f"(similarity={similarity:.3f})")
                    continue

                # Heuristic 1b — developing-story update suppression.
                # "7 killed at 6am → 12 killed at 11am" is a story updating,
                # not a contradiction. Skip unless there's a framing flip,
                # which signals a genuine narrative disagreement.
                has_framing = framing_flip(a_texts, b_texts)
                if not has_framing and is_developing_story_update(pub1, pub2, a_texts, b_texts):
                    same_story_skipped += 1
                    log.info(f"[Task12] Pair {pair_id} skipped as developing-story update")
                    continue

                # Base score: contradiction × max trust
                max_trust = max(trust_1, trust_2)
                conflict_score = contradiction_score * max_trust

                # Heuristic 2 — numeric disagreement boost
                numeric = numeric_disagreement(a_texts, b_texts)
                if numeric:
                    conflict_score = min(conflict_score + NUMERIC_BOOST, 1.0)
                    numeric_boosted += 1

                # Heuristic 3 — framing vocabulary boost
                framing = has_framing  # already computed above
                if framing:
                    conflict_score = min(conflict_score + FRAMING_BOOST, 1.0)
                    framing_boosted += 1

                # Heuristic 4 — source diversity bonus
                # Cross-region pairs (Arabic source vs Western) are more interesting
                r1 = _REGION_GROUP.get(src1, 'other')
                r2 = _REGION_GROUP.get(src2, 'other')
                cross_region = (r1 != r2 and r1 != 'other' and r2 != 'other')
                if cross_region:
                    conflict_score = min(conflict_score + DIVERSITY_BONUS, 1.0)
                    diversity_boosted += 1

                if conflict_score < CONFLICT_SCORE_THRESHOLD:
                    continue

                # Conflict type: most specific signal wins
                if numeric:
                    ctype = 'numeric'
                elif framing:
                    ctype = 'framing'
                else:
                    ctype = 'contradiction'

                cur.execute("""
                    INSERT INTO conflicts (
                        article_a_id, article_b_id,
                        conflict_type,
                        similarity_score, nli_label, nli_confidence, weighted_score
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (article_a_id, article_b_id) DO NOTHING
                """, (id1, id2,
                    ctype,
                    round(similarity, 4),
                    'contradiction',
                    round(contradiction_score, 4),
                    round(conflict_score, 4)))

                if cur.rowcount == 1:
                    conflicts_inserted += 1
                    tags = ' '.join(
                        f'[{t}]' for t, flag in
                        [('numeric', numeric), ('framing', framing), ('cross-region', cross_region)]
                        if flag
                    )
                    log.info(f"[Task12] Conflict stored {tags} — score={conflict_score:.3f} "
                             f"(sim={similarity:.3f}, contra={contradiction_score:.3f}) "
                             f"{src1}↔{src2}")

        conn.commit()

    log.info(f"[Task12] Complete — {conflicts_inserted} stored, "
             f"{same_story_skipped} same-story skipped | "
             f"numeric={numeric_boosted} framing={framing_boosted} diversity={diversity_boosted}")
    return conflicts_inserted
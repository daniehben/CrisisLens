import logging
import httpx
import os
from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

HF_NLI_URL = "https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli/pipeline/zero-shot-classification"


def classify_pair(premise: str, hypothesis: str, hf_token: str) -> dict:
    """Run NLI on a pair of headlines. Returns label and scores."""
    headers = {"Authorization": f"Bearer {hf_token}"}
    try:
        response = httpx.post(
            HF_NLI_URL,
            headers=headers,
            json={
                "inputs": premise,
                "parameters": {
                    "candidate_labels": ["contradiction", "neutral", "entailment"],
                    "hypothesis_template": hypothesis
                }
            },
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            labels = result.get("labels", [])
            scores = result.get("scores", [])
            label_scores = dict(zip(labels, scores))
            top_label = labels[0] if labels else "neutral"
            contradiction_score = label_scores.get("contradiction", 0.0)
            return {
                "label": top_label,
                "contradiction_score": contradiction_score,
                "scores": label_scores
            }
        else:
            log.warning(f"[Task11] NLI API error {response.status_code}: {response.text[:100]}")
            return {"label": "neutral", "contradiction_score": 0.0, "scores": {}}
    except Exception as e:
        log.warning(f"[Task11] NLI request failed: {e}")
        return {"label": "neutral", "contradiction_score": 0.0, "scores": {}}


def run_task11():
    log.info("[Task11] Starting NLI contradiction classification...")

    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        log.error("[Task11] HF_TOKEN not set — skipping")
        return 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ap.pair_id,
                       a1.headline_ar AS h1, a1.headline_en AS h1_en,
                       a2.headline_ar AS h2, a2.headline_en AS h2_en
                FROM article_pairs ap
                JOIN articles a1 ON a1.article_id = ap.article_id_1
                JOIN articles a2 ON a2.article_id = ap.article_id_2
                WHERE ap.status = 'pending'
                LIMIT 50
            """)
            pairs = cur.fetchall()

    if not pairs:
        log.info("[Task11] No pending pairs to classify.")
        return 0

    log.info(f"[Task11] Classifying {len(pairs)} pairs...")

    classified = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for pair_id, h1_ar, h1_en, h2_ar, h2_en in pairs:
                # use English headlines for NLI (more reliable with bart-large-mnli)
                premise = h1_en or h1_ar or ""
                hypothesis = h2_en or h2_ar or ""

                if not premise or not hypothesis:
                    continue

                result = classify_pair(premise, hypothesis, hf_token)

                cur.execute("""
                    UPDATE article_pairs
                    SET nli_label = %s,
                        contradiction_score = %s,
                        status = 'processed'
                    WHERE pair_id = %s
                """, (result["label"], result["contradiction_score"], pair_id))

                log.info(f"[Task11] Pair {pair_id}: {result['label']} "
                         f"(contradiction={result['contradiction_score']:.3f})")
                classified += 1

        conn.commit()

    log.info(f"[Task11] Complete — {classified} pairs classified")
    return classified
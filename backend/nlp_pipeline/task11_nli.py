"""Task 11 — Natural Language Inference (NLI) on candidate pairs.

We use a multilingual NLI model so we can compare Arabic headlines directly
without translating them through English first (which loses signal). The
model returns probabilities for entailment / neutral / contradiction.
"""
import logging
import os

import httpx

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

# Multilingual NLI model — supports Arabic, English, and ~100 other langs.
# ~280M params, runs on free HF Inference tier.
HF_NLI_URL = (
    "https://router.huggingface.co/hf-inference/models/"
    "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
)


def classify_pair(premise: str, hypothesis: str, hf_token: str) -> dict:
    """Returns {label, contradiction_score, scores} for one (premise, hypothesis) pair.
    Falls back to neutral/0.0 on any error so the caller can still record a row."""
    headers = {"Authorization": f"Bearer {hf_token}"}

    # mDeBERTa expects premise </s></s> hypothesis (XNLI training format)
    payload = {"inputs": f"{premise}</s></s>{hypothesis}"}

    try:
        response = httpx.post(HF_NLI_URL, headers=headers, json=payload, timeout=30)
    except Exception as e:
        log.warning(f"[Task11] NLI request failed: {e}")
        return {"label": "neutral", "contradiction_score": 0.0, "scores": {}}

    if response.status_code != 200:
        log.warning(f"[Task11] NLI API error {response.status_code}: {response.text[:120]}")
        return {"label": "neutral", "contradiction_score": 0.0, "scores": {}}

    # Response shape: [[{"label": "entailment", "score": ...}, ...]]
    # Sometimes flat: [{"label": "entailment", "score": ...}, ...]
    raw = response.json()
    if raw and isinstance(raw[0], list):
        raw = raw[0]

    scores = {item["label"].lower(): float(item["score"]) for item in raw}
    if not scores:
        return {"label": "neutral", "contradiction_score": 0.0, "scores": {}}

    top_label = max(scores, key=scores.get)
    return {
        "label": top_label,
        "contradiction_score": scores.get("contradiction", 0.0),
        "scores": scores,
    }


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
                       a1.headline_ar AS h1_ar, a1.headline_en AS h1_en,
                       a2.headline_ar AS h2_ar, a2.headline_en AS h2_en
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
                # Prefer Arabic if both pairs have it (the model is multilingual);
                # otherwise fall back to English. Mixing langs across a pair is fine.
                premise    = h1_ar or h1_en or ""
                hypothesis = h2_ar or h2_en or ""

                if not premise or not hypothesis:
                    cur.execute(
                        "UPDATE article_pairs SET status='error' WHERE pair_id=%s",
                        (pair_id,),
                    )
                    continue

                result = classify_pair(premise, hypothesis, hf_token)

                cur.execute("""
                    UPDATE article_pairs
                    SET nli_label = %s,
                        contradiction_score = %s,
                        status = 'processed'
                    WHERE pair_id = %s
                """, (result["label"], result["contradiction_score"], pair_id))

                log.info(
                    f"[Task11] Pair {pair_id}: {result['label']} "
                    f"(contradiction={result['contradiction_score']:.3f})"
                )
                classified += 1

        conn.commit()

    log.info(f"[Task11] Complete — {classified} pairs classified")
    return classified

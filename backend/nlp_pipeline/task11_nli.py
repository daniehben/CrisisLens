"""Task 11 — Natural Language Inference (NLI) on candidate pairs.

We use a multilingual NLI model (mDeBERTa-v3 XNLI) to classify whether two
articles contradict each other. Input priority: LLM-cleaned summary > raw
body snippet > headline. Using body text gives dramatically better signal than
headline-only comparison — headlines are marketing copy, contradictions live
in the reported facts.

Truncation strategy
-------------------
mDeBERTa-v3 has a 512-token context window for the combined input:

    [CLS] premise [SEP][SEP] hypothesis [SEP]   (= 3 special tokens)

We use the model's own tokenizer to measure and truncate, not character counts.
Character counts are unreliable: Arabic is ~1.5-2× denser than English per char,
so a fixed 400-char limit gives Arabic summaries ~120 tokens and English ~80.
The truncation budget is split 60/40 (premise: 290 tokens, hypothesis: 190 tokens)
because the premise is typically the more detailed/authoritative source.

This ensures the contradicting claim — usually in the final sentence of a summary
— is not silently dropped before the model sees it.

HF Inference API note
---------------------
Task11 still calls the HF Inference API (not local inference). The mDeBERTa model
is 280M params and requires ~1.1GB RAM to run locally — too large for Render's
512MB free tier. HF free tier allows ~100 req/day; at 50 pairs/cycle this is
2 full cycles before hitting the cap. A circuit breaker is not yet implemented
here. See docs/BUDGET.md for the local inference upgrade path when RAM allows.
"""
import logging
import os
from functools import lru_cache
from typing import Optional

import httpx

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

HF_NLI_URL = (
    "https://router.huggingface.co/hf-inference/models/"
    "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    "/pipeline/text-classification"
)

# Token budget for each side of the NLI pair.
# Total must be <= 512 - 3 special tokens = 509.
# 60/40 split: premise gets more room (typically the reference source).
PREMISE_TOKEN_BUDGET    = 290
HYPOTHESIS_TOKEN_BUDGET = 190


@lru_cache(maxsize=1)
def _get_tokenizer():
    """
    Load the mDeBERTa tokenizer once per process (cached).
    Used only for token-aware truncation — no model weights loaded,
    so memory cost is negligible (~10MB vocab files).
    Falls back to None if transformers is not installed.
    """
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(
            "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
        )
        log.info("[Task11] NLI tokenizer loaded.")
        return tok
    except Exception as e:
        log.warning(f"[Task11] Could not load tokenizer: {e} — falling back to char truncation")
        return None


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """
    Truncate text so it fits within max_tokens using the model's own tokenizer.
    Falls back to a conservative character estimate (max_tokens * 3 chars) if
    the tokenizer is unavailable.
    """
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        # Fallback: 1 token ≈ 3 chars average across Arabic/English
        return text[: max_tokens * 3]

    ids = tokenizer.encode(text, add_special_tokens=False)
    if len(ids) <= max_tokens:
        return text

    # Decode the truncated token ids back to a string
    truncated_ids = ids[:max_tokens]
    return tokenizer.decode(truncated_ids, skip_special_tokens=True, clean_up_tokenization_spaces=True)


def _best_text(summary: Optional[str], body: Optional[str],
               hl_ar: Optional[str], hl_en: Optional[str]) -> Optional[str]:
    """
    Pick the richest available text for one article side.
    Priority: summary (LLM-cleaned facts) > body_snippet > headline.
    Returns None only if all fields are empty.
    """
    for candidate in (summary, body, hl_ar, hl_en):
        if candidate and len(candidate.strip()) > 20:
            return candidate.strip()
    return None


def classify_pair(premise: str, hypothesis: str, hf_token: str) -> dict:
    """
    Run NLI on one (premise, hypothesis) pair.
    Returns {label, contradiction_score, scores}.
    Falls back to neutral/0.0 on any error so the caller can still record a row.
    """
    headers = {"Authorization": f"Bearer {hf_token}"}

    # mDeBERTa expects premise </s></s> hypothesis (XNLI training format).
    # top_k=None returns scores for ALL classes.
    payload = {
        "inputs": f"{premise}</s></s>{hypothesis}",
        "parameters": {"top_k": None},
    }

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
                       a1.summary     AS h1_sum, a1.body_snippet AS h1_body,
                       a2.headline_ar AS h2_ar, a2.headline_en AS h2_en,
                       a2.summary     AS h2_sum, a2.body_snippet AS h2_body,
                       a1.published_at AS pub1,  a2.published_at AS pub2
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
            for (pair_id,
                 h1_ar, h1_en, h1_sum, h1_body,
                 h2_ar, h2_en, h2_sum, h2_body,
                 pub1, pub2) in pairs:

                raw_premise    = _best_text(h1_sum, h1_body, h1_ar, h1_en)
                raw_hypothesis = _best_text(h2_sum, h2_body, h2_ar, h2_en)

                if not raw_premise or not raw_hypothesis:
                    cur.execute(
                        "UPDATE article_pairs SET status='error' WHERE pair_id=%s",
                        (pair_id,),
                    )
                    continue

                # Truncate using the tokenizer's own vocabulary, not character counts.
                # This preserves whole words/subwords and never cuts mid-token.
                premise    = _truncate_to_tokens(raw_premise,    PREMISE_TOKEN_BUDGET)
                hypothesis = _truncate_to_tokens(raw_hypothesis, HYPOTHESIS_TOKEN_BUDGET)

                result = classify_pair(premise, hypothesis, hf_token)

                cur.execute("""
                    UPDATE article_pairs
                    SET nli_label           = %s,
                        contradiction_score = %s,
                        status              = 'processed'
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

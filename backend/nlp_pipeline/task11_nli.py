"""Task 11 — NLI contradiction classification via Groq LLM.

Previously used HF Inference API (mDeBERTa-v3-base-xnli, 280M params) with a
~100 req/day free-tier limit. At 50 pairs/cycle × 96 cycles/day, this caused
the conflict queue to stall after the first 2 cycles every day. Replaced with
Groq FAST_MODEL (llama-3.1-8b-instant, 14,400 req/day free) — 144× the daily
capacity with no local RAM cost.

Label mapping (matches task12 expectations):
  contradiction → sources make conflicting factual claims about the same event
  entailment    → one article's facts are consistent with / follow from the other
  neutral       → different events, insufficient overlap, or no clear relationship

contradiction_score is the LLM confidence (0.0–1.0) when label=contradiction,
0.0 otherwise. Compatible with task12's CONTRADICTION_THRESHOLD = 0.55.
"""
import json
import logging

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat, FAST_MODEL

log = logging.getLogger(__name__)

BATCH_SIZE = 50   # pairs per cycle — well within Groq's 14,400/day free cap

PROMPT = """\
You are a news contradiction classifier. Two news excerpts are provided below. \
Classify their relationship and estimate your confidence.

Article A:
{premise}

Article B:
{hypothesis}

Output a single JSON object with exactly two keys:
- "label": one of "contradiction", "entailment", "neutral"
  - "contradiction" = the articles make conflicting factual claims about the same event \
(e.g. different casualty numbers, contradictory attributions, disputed outcomes)
  - "entailment"    = one article's claims are consistent with or supported by the other's
  - "neutral"       = they cover different events, or there is insufficient overlap to judge
- "confidence": float 0.0–1.0 representing how certain you are of the label

Be strict: only use "contradiction" when there is a genuine factual conflict, not just \
different emphasis or framing. Output JSON only, no markdown."""


def _best_text(summary, body, hl_ar, hl_en):
    for candidate in (summary, body, hl_ar, hl_en):
        if candidate and len(candidate.strip()) > 20:
            return candidate.strip()
    return None


def _classify(premise: str, hypothesis: str) -> dict:
    """
    Classify one pair via Groq. Returns {label, contradiction_score}.
    Falls back to neutral/0.0 on any failure so the caller can still record the row.
    """
    prompt = PROMPT.format(
        premise=premise[:1200],       # generous but keeps prompt tight
        hypothesis=hypothesis[:1200],
    )
    raw = chat(prompt, model=FAST_MODEL, max_tokens=80, json_mode=True)
    if not raw:
        return {"label": "neutral", "contradiction_score": 0.0}

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"label": "neutral", "contradiction_score": 0.0}

    label = result.get("label", "neutral").lower()
    if label not in ("contradiction", "entailment", "neutral"):
        label = "neutral"
    confidence = float(result.get("confidence", 0.0))
    score = confidence if label == "contradiction" else 0.0
    return {"label": label, "contradiction_score": score}


def run_task11():
    log.info("[Task11] Starting NLI classification (Groq)...")

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
                LIMIT %s
            """, (BATCH_SIZE,))
            pairs = cur.fetchall()

    if not pairs:
        log.info("[Task11] No pending pairs.")
        return 0

    log.info(f"[Task11] Classifying {len(pairs)} pairs via Groq...")

    classified = 0
    contradictions = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for (pair_id,
                 h1_ar, h1_en, h1_sum, h1_body,
                 h2_ar, h2_en, h2_sum, h2_body,
                 pub1, pub2) in pairs:

                premise    = _best_text(h1_sum, h1_body, h1_ar, h1_en)
                hypothesis = _best_text(h2_sum, h2_body, h2_ar, h2_en)

                if not premise or not hypothesis:
                    cur.execute(
                        "UPDATE article_pairs SET status='error' WHERE pair_id=%s",
                        (pair_id,),
                    )
                    continue

                result = _classify(premise, hypothesis)

                cur.execute("""
                    UPDATE article_pairs
                    SET nli_label           = %s,
                        contradiction_score = %s,
                        status              = 'processed'
                    WHERE pair_id = %s
                """, (result["label"], result["contradiction_score"], pair_id))

                if result["label"] == "contradiction":
                    contradictions += 1
                classified += 1
                log.info(
                    f"[Task11] Pair {pair_id}: {result['label']} "
                    f"(score={result['contradiction_score']:.3f})"
                )

        conn.commit()

    log.info(f"[Task11] Complete — {classified} classified, {contradictions} contradictions")
    return classified

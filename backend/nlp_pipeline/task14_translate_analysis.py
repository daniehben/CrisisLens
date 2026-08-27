"""Task 14 — Translate bias analysis fields to Arabic using Groq.

For each conflict that has a framing_analysis but no Arabic translations,
sends all 4 fields in a single Groq JSON call (efficient) and merges the
results back into the existing framing_analysis JSONB:

  framing_analysis = {
    "claims_a":                "...",          # existing English
    "claims_b":                "...",
    "factual_disagreement":    "..." | null,
    "framing_difference":      "..." | null,
    "claims_a_ar":             "...",          # added by this task
    "claims_b_ar":             "...",
    "factual_disagreement_ar": "..." | null,
    "framing_difference_ar":   "..." | null
  }

Falls back to Google Translate per-field if Groq is unavailable.
Runs after task13 in the scheduler.
"""
import json
import logging

import psycopg2.extras
from deep_translator import MyMemoryTranslator

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat_json, FAST_MODEL

log = logging.getLogger(__name__)

BATCH_SIZE = 20

_PROMPT = """\
Translate the following news analysis fields from English to natural Modern \
Standard Arabic (MSA) as used in professional Arabic journalism. \
Return a JSON object with the same keys but Arabic values. \
For any field whose value is null, return null for that key too. \
Output ONLY valid JSON, no other text.

{input_json}"""


def _translate_via_mymemory(fields: dict) -> dict:
    """Per-field MyMemory fallback. Returns dict with _ar keys.

    MyMemory (deep_translator.MyMemoryTranslator) is a free public translation
    API that doesn't require an API key and doesn't use Google's unofficial
    scraping endpoint. It replaced the GoogleTranslator fallback after
    deep_translator's GoogleTranslator started returning TranslationNotFound
    for all inputs (2026-08).

    Limits: ~500 words/request, ~1000 words/day on the anonymous tier.
    Acceptable for a fallback that only fires when Groq is unavailable.
    """
    result = {}
    for key, value in fields.items():
        if not value:
            result[f"{key}_ar"] = value
            continue
        try:
            translated = MyMemoryTranslator(source='en-US', target='ar-SA').translate(value)
            result[f"{key}_ar"] = translated or value
        except Exception as e:
            log.warning(f"[Task14] MyMemory fallback failed for {key}: {e}")
            result[f"{key}_ar"] = value          # keep English rather than drop the field
    return result


def run_task14():
    log.info("[Task14] Translating bias analysis fields to Arabic...")

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT conflict_id, framing_analysis
                FROM conflicts
                WHERE framing_analysis IS NOT NULL
                  AND (
                    (framing_analysis->>'claims_a_ar') IS NULL
                    OR (framing_analysis->>'narrative' IS NOT NULL
                        AND (framing_analysis->>'narrative_ar') IS NULL)
                  )
                ORDER BY weighted_score DESC
                LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task14] No bias analyses need Arabic translation.")
        return 0

    log.info(f"[Task14] Translating {len(rows)} conflict analyses...")

    written = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                ba = row["framing_analysis"]
                if isinstance(ba, str):
                    try:
                        ba = json.loads(ba)
                    except Exception:
                        continue

                fields_to_translate = {
                    "dispute":              ba.get("dispute"),
                    "narrative":            ba.get("narrative"),
                    "claims_a":             ba.get("claims_a"),
                    "claims_b":             ba.get("claims_b"),
                    "key_question":         ba.get("key_question"),
                    "factual_disagreement": ba.get("factual_disagreement"),
                    "framing_difference":   ba.get("framing_difference"),
                }

                # Single Groq JSON call for all 4 fields
                input_json = json.dumps(fields_to_translate, ensure_ascii=False)
                ar_fields = chat_json(
                    _PROMPT.format(input_json=input_json),
                    model=FAST_MODEL,
                    max_tokens=800,
                )

                if ar_fields and ("claims_a" in ar_fields or "narrative" in ar_fields):
                    # Groq returns same keys — remap to _ar variants
                    for key in fields_to_translate:
                        if key in ar_fields:
                            ba[f"{key}_ar"] = ar_fields[key]
                else:
                    log.info(f"[Task14] Conflict {row['conflict_id']}: Groq unavailable, using MyMemory fallback")
                    ar = _translate_via_mymemory(fields_to_translate)
                    ba.update(ar)

                cur.execute(
                    "UPDATE conflicts SET framing_analysis = %s WHERE conflict_id = %s",
                    (json.dumps(ba, ensure_ascii=False), row["conflict_id"]),
                )
                written += 1

            conn.commit()

    log.info(f"[Task14] Complete — {written} analyses translated")
    return written

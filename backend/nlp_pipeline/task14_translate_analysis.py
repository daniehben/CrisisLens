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

Falls back to MyMemory per-field if Groq is unavailable.
Runs after task13 in the scheduler.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import psycopg2.extras
from deep_translator import MyMemoryTranslator

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat_json, FAST_MODEL

log = logging.getLogger(__name__)

BATCH_SIZE = 20

# Module-level daily-quota freeze for MyMemory.
# When the anonymous 200 k char/day limit is hit, we set this to the next
# midnight UTC so every subsequent call in the same process skips MyMemory
# entirely (rather than burning 20 failing requests per cycle).
_mymemory_frozen_until: float = 0.0
_mymemory_freeze_lock = threading.Lock()


def _mymemory_is_frozen() -> bool:
    return time.time() < _mymemory_frozen_until


def _set_mymemory_day_freeze() -> None:
    """Freeze MyMemory until the next midnight UTC."""
    global _mymemory_frozen_until
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = midnight.replace(day=midnight.day + 1)
    reset_ts = next_midnight.timestamp()
    with _mymemory_freeze_lock:
        _mymemory_frozen_until = reset_ts
    log.warning(
        f"[Task14] MyMemory daily quota exhausted — "
        f"freezing until {next_midnight.isoformat()} UTC. "
        "Fallback translation skipped for rest of today."
    )

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

    Limits: ~500 words/request, ~200 k chars/day on the anonymous tier.
    When the daily limit is hit the function sets a module-level freeze until
    midnight UTC and returns English values rather than burning requests on
    every subsequent pipeline cycle.

    Rate: always sleep 0.35s before every request (including the first).
    This caps throughput at ~2.86 req/s, safely below MyMemory's 5 req/s
    limit even when many conflicts hit the fallback simultaneously.
    """
    _MYMEMORY_MAX_CHARS = 490
    _INTER_REQUEST_SLEEP = 0.35   # always, even before first: ~2.86 req/s

    # Fast-path: skip entirely if daily quota already exhausted this process-day
    if _mymemory_is_frozen():
        log.debug("[Task14] MyMemory day-freeze active — returning English values")
        return {f"{key}_ar": value for key, value in fields.items()}

    result = {}
    for key, value in fields.items():
        if not value:
            result[f"{key}_ar"] = value
            continue

        # Re-check freeze inside loop: a previous field may have hit the limit
        if _mymemory_is_frozen():
            log.debug(f"[Task14] MyMemory day-freeze triggered mid-batch, skipping {key}")
            result[f"{key}_ar"] = value
            continue

        time.sleep(_INTER_REQUEST_SLEEP)
        text = value[:_MYMEMORY_MAX_CHARS] if len(value) > _MYMEMORY_MAX_CHARS else value
        try:
            translated = MyMemoryTranslator(source='en-US', target='ar-SA').translate(text)
            result[f"{key}_ar"] = translated or value
        except Exception as e:
            err_str = str(e).upper()
            if ("DAILY" in err_str or "LIMIT" in err_str or "QUOTA" in err_str
                    or "429" in err_str or "TOO MANY" in err_str):
                _set_mymemory_day_freeze()
                result[f"{key}_ar"] = value      # keep English for this and remaining fields
            else:
                log.warning(f"[Task14] MyMemory fallback failed for {key}: {e}")
                result[f"{key}_ar"] = value      # keep English rather than drop the field
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

                # Truncate each field before sending to Groq so the output JSON
                # is bounded. 300 chars → ~75 tokens per field; 7 fields + JSON
                # overhead ≈ 600 tokens out — well within 1 600 budget.
                # Previously max_tokens=800 was too tight for longer fields,
                # causing "max completion tokens reached" 400 errors.
                _GROQ_FIELD_MAX = 300
                fields_for_groq = {
                    k: (v[:_GROQ_FIELD_MAX] if isinstance(v, str) else v)
                    for k, v in fields_to_translate.items()
                }
                input_json = json.dumps(fields_for_groq, ensure_ascii=False)
                ar_fields = chat_json(
                    _PROMPT.format(input_json=input_json),
                    model=FAST_MODEL,
                    max_tokens=1600,   # was 800 — raised to cover 7 Arabic fields
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
                    time.sleep(1.0)  # inter-conflict gap: without this, first field of N+1 fires immediately after last field of N

                cur.execute(
                    "UPDATE conflicts SET framing_analysis = %s WHERE conflict_id = %s",
                    (json.dumps(ba, ensure_ascii=False), row["conflict_id"]),
                )
                written += 1

            conn.commit()

    log.info(f"[Task14] Complete — {written} analyses translated")
    return written

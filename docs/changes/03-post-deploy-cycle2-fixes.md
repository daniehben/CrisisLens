# Change Log 03 — Second Deployment Cycle Fixes

**Date:** 2026-08-27  
**Status:** Ready to deploy  
**Scope:** Bugs surfaced by the 12:31 and 12:46 UTC log cycles after the Log 02 fixes went live.  
**Preceded by:** `02-post-first-run-fixes.md`  
**Read alongside:** `decisions/decision-trail-cycle2.md`

---

## 1. Task13 max_tokens — `backend/nlp_pipeline/task13_bias_analysis.py`

**What:** `max_tokens` increased 1200 → 1500 on both the SMART_MODEL and FAST_MODEL fallback calls.

**Why:** The prompt now outputs 9 fields including `emotion_a` and `emotion_b`. For conflicts with a long narrative and non-null factual/framing fields, the JSON was hitting the ceiling before the closing brace.

**Log line that caught it:**
```
json_validate_failed — Failed to generate JSON: max completion tokens reached before generating a valid document
```

---

## 2. MyMemory rate limit — `backend/nlp_pipeline/task14_translate_analysis.py`

**What:** Added `time.sleep(0.25)` between field translations in `_translate_via_mymemory()`.

**Why:** MyMemory's free tier caps at ~5 req/s. Translating 7 fields per conflict with no delay fires them all in under 1 second — fields 2+ get rate-limited and silently stay in English.

**Log line that caught it:**
```
[Task14] MyMemory fallback failed for claims_b: Server Error: You made too many requests to the server.
```

---

## 3. MyMemory 500-char limit — `backend/nlp_pipeline/task14_translate_analysis.py`

**What:** Truncate text to 490 chars before each MyMemory call (`_MYMEMORY_MAX_CHARS = 490`).

**Why:** MyMemory rejects requests over 500 chars. The `narrative` field from task13 regularly runs 600–900 chars and was being rejected silently, keeping English text in the Arabic field.

**Log line that caught it:**
```
[Task14] MyMemory fallback failed for narrative: Text length need to be between 0 and 500 characters
```

---

## 4. Shared circuit breaker — `backend/shared/groq_client.py`

**What:** Replaced the single `_groq_cb` shared across all models with `_groq_cbs: dict[str, CircuitBreaker]` and a `_get_cb(model)` lazy accessor.

**Why:** Task13 calls SMART_MODEL for 5 conflicts per cycle. Per-minute rate-limit 429s (not TPD) trip the shared breaker. 15 minutes later task7.5 is the first FAST_MODEL caller — the HALF_OPEN probe fires, hits another 429, the breaker re-opens, and all 15 task7.5 calls return None immediately. Root cause of "0 summarized, 15 failed" every cycle.

Per-model breakers mean SMART_MODEL failures from task13 no longer affect task7.5, task8b, or task14.

**What `get_groq_cb_status()` now returns:**
```json
{
  "openai/gpt-oss-20b":  {"state": "CLOSED", "failure_count": 0},
  "openai/gpt-oss-120b": {"state": "OPEN",   "failure_count": 5}
}
```

---

## Files Changed in Log 03

| File | Change |
|---|---|
| `backend/nlp_pipeline/task13_bias_analysis.py` | max_tokens 1200 → 1500 |
| `backend/nlp_pipeline/task14_translate_analysis.py` | MyMemory rate limit sleep + 490-char truncation |
| `backend/shared/groq_client.py` | Per-model circuit breakers |

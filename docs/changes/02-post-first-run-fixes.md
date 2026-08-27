# Change Log 02 — Post First-Run Fixes

**Date:** 2026-08-27  
**Status:** Deployed  
**Scope:** Bugs surfaced by reading live Railway worker logs after the Phase 1 deploy. No new features — these are corrections to things that looked fine in code review but broke in the actual production environment.  
**Preceded by:** `01-phase1-reliability-quality.md`  
**Read alongside:** `decisions/decision-trail-post-first-run.md` (the reasoning behind each fix)

The source of truth for these fixes was the Railway log output from three ingestion cycles (12:04, 12:15, 12:30 UTC on 2026-08-26). Every change here traces back to a specific log line.

---

## 1. AJA URL — `backend/ingestion_worker/adapters/rss_adapter.py`

**What:** `aljazeera.net/rss/all.xml` changed to Google News proxy:  
`https://news.google.com/rss/search?q=site:aljazeera.net&hl=ar&gl=QA&ceid=QA:ar`

**Why:** The direct RSS URL was returning HTTP 404 in production on every cycle. This meant AJA — the highest-trust source in the system (0.90) — was contributing 0 articles per cycle. The Google News proxy pattern was already in use for F24, ARB, WAF, MAYE, and SDT for the same reason (Render Frankfurt IP blocks). This extends the same fix to AJA.

`gl=QA` (Qatar) because Al Jazeera is Qatar-based — the Qatar geo-locale returns the broadest Arabic edition coverage from Google News.

**Log line that caught it:**
```
HTTP error 404: https://www.aljazeera.net/rss/all.xml
```

**Impact:** AJA was the most important source in the pipeline (trust 0.90, primary Arabic conflict signal). Every cycle it ran dead was a full cycle with no Al Jazeera content entering conflict pair generation.

---

## 2. Groq TPD Freeze — `backend/shared/groq_client.py`

### 2a. Error-triggered freeze

**What:** When a Groq API call fails with a `tokens per day` 429 error, the client now parses the `try again in Xh Ym Zs` string from the error message and freezes all calls to that model until that exact time (falling back to end-of-day UTC if unparseable). A frozen model returns `None` immediately without touching the circuit breaker or the API.

**Why:** The circuit breaker pattern (CLOSED → OPEN after 5 failures → HALF_OPEN probe every 5 min) is the right tool for transient outages. It is the wrong tool for daily token quota exhaustion. A TPD limit is deterministic — retrying before the reset time is guaranteed to fail, and the probe attempt itself consumes a token from tomorrow's budget if the quota has already rolled over. The old behaviour generated continuous log churn and wasted quota; the new behaviour logs one warning and goes silent until the reset.

**Log lines that caught it:**
```
Limit 200000, Used 200000, Requested ~450. Please try again in 3h17m8.64s.
[groq] circuit breaker: HALF_OPEN → probe → OPEN (repeat every 5 min)
```

### 2b. Proactive budget check from response headers

**What:** Every successful Groq call now uses `client.with_raw_response.chat.completions.create()` instead of `client.chat.completions.create()`. The raw response exposes HTTP headers. After each successful call, `x-ratelimit-remaining-tokens-day` and `x-ratelimit-limit-tokens-day` are read and stored in module-level state (`_tpd_remaining`, `_tpd_limit`).

Before each new call, `_has_token_budget(model, max_tokens)` checks whether `remaining >= max_tokens * 2`. If not, the call is skipped with a warning before touching the Groq API — the 429 never happens.

The `* 2` factor covers prompt (input) tokens we can't know exactly in advance. For a 400-token output with a 300-token prompt, total ≈ 700 tokens — well within 2×. The minimum reserve floor is 2,000 tokens regardless of `max_tokens`.

**Why this is better than the freeze-only approach:** The freeze only activates after a 429 has already been hit. The proactive check stops calls before the limit is reached at all — graceful degradation instead of a cliff edge. The real numbers come from Groq's own headers, not from our internal call counter (which counts requests, not tokens).

**What you see in logs after this change:**
```
[groq] openai/gpt-oss-20b: 45,231 / 200,000 tokens remaining today (22.6%)
[groq] openai/gpt-oss-20b: only 1,200 tokens remaining today, need ~800 — skipping to avoid 429.
```

**What the health endpoint now returns** (under `groq_usage`):
```json
{
  "openai/gpt-oss-20b": {
    "calls_today": 87,
    "calls_cap": 50000,
    "tokens_remaining": 45231,
    "tokens_limit": 200000,
    "tokens_pct": 22.6,
    "tpd_frozen": false
  }
}
```

`tokens_remaining` and `tokens_limit` come directly from Groq's response headers and are accurate to the last successful call, even after a worker restart that wipes the local call counter.

---

## 3. Task14 Translate Fallback — `backend/nlp_pipeline/task14_translate_analysis.py`

**What:** `deep_translator.GoogleTranslator` replaced with `deep_translator.MyMemoryTranslator` in the `_translate_via_google` function (renamed `_translate_via_mymemory`).

**Why:** `deep_translator.GoogleTranslator` uses an unofficial Google scraping endpoint that began returning `TranslationNotFound` for every field as of 2026-08. The exception was being caught and the field was silently falling back to its English value, but the translation step was functionally dead.

`MyMemoryTranslator` is available in the same `deep_translator` package, requires no API key, and uses a proper public API endpoint rather than scraping. Free tier handles ~500 words/request and ~1,000 words/day — appropriate for a fallback that only fires when Groq (the primary path) is unavailable.

**Note:** This fallback only activates when Groq is down or frozen. With the TPD freeze logic in place, Groq will be the primary for every call until the daily budget is exhausted. The fallback matters most during Groq's reset window.

**Log lines that caught it:**
```
[Task14] Google fallback failed for claims_a: No translation was found using the current translator. Try another translator?
[Task14] Google fallback failed for claims_b: No translation was found using the current translator. Try another translator?
```

---

## Known Issues Not Fixed Here

**TAS (Tasnim News Arabic) — 0 entries:** `tasnimnews.com` is not indexed by Google News across any geo/hl combination tested (IR:ar and EG:ar). This was marginal before Phase 1 and remains 0. Not a regression. Carried to `BACKLOG.md`.

**task7 body fetch — 0/25:** `trafilatura` logs `discarding data: None` for all Google News redirect URLs. Google News redirect URLs don't resolve to article pages that trafilatura can parse — this is a pre-existing architectural limitation, not a new bug. Body text is sourced from RSS `body_snippet` for sources served via Google News proxy. Carried to `BACKLOG.md`.

**Groq TPD limit itself:** The 200,000 tokens/day ceiling is a Groq account-level limit. The fixes in this log make the system behave gracefully when it's hit, but the ceiling itself remains. If NLP enrichment (task7.5 summaries, task13 bias analysis) going offline during peak hours becomes a recurring operational issue, the fix is upgrading the Groq plan to a higher TPD tier.

---

## Files Changed in Log 02

| File | Type |
|---|---|
| `backend/ingestion_worker/adapters/rss_adapter.py` | Modified |
| `backend/shared/groq_client.py` | Modified |
| `backend/nlp_pipeline/task14_translate_analysis.py` | Modified |

# Decision Trail — Post First-Run Testing

**Date:** 2026-08-27  
**Scope:** Every decision made after reading the first live Railway worker logs. The Phase 1 deploy ran successfully at the ingestion level, but the logs surfaced four bugs that weren't visible in code review. This document records what triggered each fix and why each fix took the shape it did.  
**Read alongside:** `changes/02-post-first-run-fixes.md` (what changed) and `decisions/decision-trail-phase1.md` (the Phase 1 baseline this builds on).

The format is the same as Phase 1: **what was observed → what that triggered → what the outcome was.**

---

## 1. User pasted Railway logs with no question → structured log analysis

After the Phase 1 deploy went live on Railway, the user shared three cycles of worker logs (12:04, 12:15, 12:30 UTC on 2026-08-26) without a specific question attached.

**What the logs showed:**

| Area | Status | Detail |
|---|---|---|
| Ingestion — all 5 new sources | ✅ Healthy | HAA, TNA, ASH, PCH, IMEMC all fetching and inserting |
| Ingestion — AJA | ❌ Dead | `HTTP error 404: aljazeera.net/rss/all.xml` every cycle |
| Jina embeddings (task9) | ✅ Healthy | 100/100 stored every cycle |
| Groq FAST_MODEL | ❌ TPD exhausted | `Limit 200000, Used 200000` — circuit breaker cycling OPEN/HALF_OPEN every 5 min |
| task13 bias analysis | ❌ 0 results | Groq TPD exhaustion blocking all LLM calls |
| task14 Arabic translation | ❌ Broken | `deep_translator` Google fallback: `TranslationNotFound` for every field |
| TAS (Tasnim Arabic) | ⚠️ 0 entries | Not indexed by Google News — pre-existing, not a regression |
| task7 body fetch | ⚠️ 0/25 | trafilatura can't follow Google News redirects — pre-existing architectural limit |

**Decision:** Treat the logs as a prioritised bug list. Fix AJA first (highest-trust source, zero articles), then Groq TPD (blocking all NLP enrichment), then the translate fallback. The TAS and task7 issues are pre-existing and go to `BACKLOG.md` rather than immediate fixes.

---

## 2. "AJA 404" → Google News proxy (same pattern as F24/ARB/WAF)

**Observation:** `aljazeera.net/rss/all.xml` returns HTTP 404 on every cycle. This URL was the Arabic feed we switched to in Phase 1 (replacing the English `aljazeera.net/en`). It appeared valid in testing but is dead in production.

**Question triggered:** Is the URL wrong, or is the server blocking Render's Frankfurt IP?

**Investigation:** The URL `www.aljazeera.net/rss/all.xml` simply doesn't exist — it's a 404, not a 403. Al Jazeera's Arabic RSS structure may have changed since the URL was sourced. The Google News proxy (`news.google.com/rss/search?q=site:aljazeera.net&hl=ar&gl=QA&ceid=QA:ar`) was already the fix pattern for every other source that had IP or URL issues.

**Decision:** Switch AJA to Google News proxy rather than hunt for the correct direct RSS path. `gl=QA` (Qatar) because Al Jazeera is headquartered in Doha — the Qatar geo-locale returns the most complete coverage of the Arabic edition. The trust weight (0.90) is unchanged. This is consistent with how five other sources in Phase 1 were handled.

**Outcome:** AJA URL changed in `rss_adapter.py`. Expected result after next deploy: AJA entries showing 50–100 articles per cycle.

---

## 3. Groq circuit breaker cycling on TPD → freeze-until-reset pattern

**Observation:** Every cycle showed `Limit 200000, Used 200000` in the Groq rate limit error, followed by the circuit breaker opening, probing after 5 minutes, hitting the 429 again, and re-opening. This repeated indefinitely.

**Question triggered:** Is the circuit breaker the right tool for a daily token quota exhaustion?

**Investigation:** The circuit breaker was designed for transient failures — a temporary Groq outage, a network blip, a rate limit spike that clears in seconds. A daily token limit is fundamentally different: it is deterministic. Retrying before the quota resets is guaranteed to fail. Worse, the circuit breaker's HALF_OPEN probe attempt is itself a Groq API call — it consumes a token from the (possibly already reset) quota on each probe cycle. The error message contains the exact retry time: `try again in 3h17m8.64s`.

**Decision:** Add a separate TPD freeze mechanism that:
1. Detects `tokens per day` in the error string (distinct from transient 429s which say `rate_limit_exceeded` without that phrase)
2. Parses the retry window from the error message
3. Sets a `_tpd_frozen_until[model]` timestamp — calls to that model return `None` immediately until the timestamp passes
4. Does NOT count the error as a circuit breaker failure (the CB is for transient errors; TPD is quota exhaustion)

**Why not just increase the circuit breaker cooldown?** Because the retry time varies — 3 hours, 7 hours, or all the way to midnight depending on when in the day the limit was hit. A fixed cooldown would either be too short (re-opening before the reset) or too long (staying dark after the reset). Parsing the actual retry window from Groq's error message is the correct approach.

**Outcome:** `_is_tpd_frozen()` and `_set_tpd_freeze()` added to `groq_client.py`. The freeze check runs before the circuit breaker check in `chat()`.

---

## 4. "How to check Groq token usage — is there no code I can write?" → real-time header reading

**Question asked:** Is there a way to get a message about remaining token usage so the system can act on it before hitting the limit, rather than only reacting after a 429?

**Investigation:** Groq's API returns two HTTP response headers on every successful call:
- `x-ratelimit-remaining-tokens-day` — exact tokens remaining in today's budget
- `x-ratelimit-limit-tokens-day` — the total daily ceiling

These are the authoritative numbers, sent by Groq on every response. The Groq Python SDK's `with_raw_response` accessor returns the HTTP response object (including headers) alongside the parsed completion, without any extra API calls.

**Decision:** Switch every `client.chat.completions.create()` call to `client.with_raw_response.chat.completions.create()`. After each successful call, parse and store the headers in `_tpd_remaining[model]` and `_tpd_limit[model]`. Add a proactive check before each call: if `remaining < max_tokens * 2`, skip the call with a warning rather than letting it fail.

The `* 2` multiplier covers prompt (input) tokens we can't know precisely before the call. A 400-token output with a 300-token prompt totals ~700 tokens, well within 2×. The floor is `_MIN_TOKENS_RESERVE = 2,000` tokens regardless.

**Why this is better than counting calls internally:** Our previous daily counter tracked API *requests*, not *tokens*. A call that generates a 600-token output costs more than one that generates 50 tokens — the request counter treated them identically. The header numbers are token-accurate and automatically reset with Groq's own clock.

**Secondary outcome:** `get_daily_usage()` now returns `tokens_remaining`, `tokens_limit`, and `tokens_pct` from the stored header values, exposed on the health endpoint. A single `GET /health` call shows the real budget state without SSH access to logs.

**What changes in log behaviour:**
- Before: silent until 429, then circuit breaker churn
- After: `[groq] openai/gpt-oss-20b: 45,231 / 200,000 tokens remaining today (22.6%)` after each call; one warning when below 20%; a single skip log when budget is too low for the next call

---

## 5. "Update the documentation under a post-first-run title" → this file + 02 change log

**Decision:** Create two documents parallel to the Phase 1 docs:
- `docs/changes/02-post-first-run-fixes.md` — what changed (file-level, with log lines that triggered each change)
- `docs/decisions/decision-trail-post-first-run.md` — why each change took the shape it did (this file)

The numbered prefix on the change log (`02-`) makes the sequence explicit: any future change log that touches the same files should read `02` before `01`.

---

## Standing Practices Updated

The Phase 1 standing practices hold. One addition from this session:

- **Deploy = watch the first three cycles of logs** — code review catches logical bugs; live logs catch environmental ones (IP blocks, dead URLs, API quota behaviour). AJA's 404 and the Groq TPD cycling were both invisible in code review but obvious in the first 30 minutes of logs.

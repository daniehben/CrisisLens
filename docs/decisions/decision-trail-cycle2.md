# Decision Trail — Second Deployment Cycle

**Date:** 2026-08-27  
**Scope:** Decisions from the 12:31 and 12:46 UTC log cycles — first cycles after Log 02 fixes went live.  
**Read alongside:** `changes/03-post-deploy-cycle2-fixes.md`

---

## 1. Task13 json_validate_failed → max_tokens 1200→1500

Prompt now outputs 9 fields including emotion_a/emotion_b added in a prior session. For complex conflicts the JSON hits the ceiling before closing. 1500 covers the expanded output without burning extra TPD on every call — the narrative field is bounded to 2–3 sentences so pathologically long output is a compliance failure, not a token failure.

## 2. MyMemory rate limit → sleep(0.25)

MyMemory free tier: ~5 req/s. 7 fields per conflict with no delay = 7 requests in under 1 second. Fix: 0.25s sleep between fields (skipped before the first). That gives ~4 req/s with margin for latency variation. Batching all fields into one request was rejected — MyMemory takes a single string, not JSON, so splitting the translation back into fields is unreliable.

## 3. MyMemory 500-char limit → truncate to 490

Narrative field runs 600–900 chars. MyMemory hard-rejects over 500. Truncating at 490 (10-char buffer for encoding edge cases) means the Arabic translation covers most of the text rather than none of it. Splitting across multiple calls was rejected — Arabic sentence-boundary detection without an NLP library produces unnatural joins.

## 4. Shared circuit breaker → per-model

Single breaker meant task13 SMART_MODEL 429s opened the breaker and blocked task7.5 FAST_MODEL calls in the next cycle ("0 summarized, 15 failed"). Per-model breakers isolate failure domains. The rule: circuit breakers that span multiple independent resource tiers belong per-tier.

# Change Log 01 — Phase 1: Reliability & Quality

**Date:** 2026-08  
**Status:** Deployed  
**Scope:** Backend reliability hardening, NLP quality improvements, source routing fixes, new source activation

All subsequent change logs build on this baseline. If a later change touches any of the files listed here, read this document first to understand the original intent.

---

## 1. Circuit Breaker — `backend/shared/circuit_breaker.py` *(new file)*

**What:** Thread-safe circuit breaker implementing CLOSED → OPEN → HALF_OPEN state machine.

**Why:** Groq and Jina are external paid APIs. Without a circuit breaker, a temporary outage causes the worker to hammer failing endpoints every 15 minutes — burning rate limit quota, cluttering logs, and potentially incurring costs on a degraded service. The circuit breaker detects a failure streak, stops calling the API for a cooldown window, then tests with a single probe before fully reopening.

**Configuration:**
- Groq: `failure_threshold=5, cooldown_s=300` — lenient because Groq is called at high volume per cycle
- Jina: `failure_threshold=3, cooldown_s=180` — tighter because a single bad embedding batch blocks the entire NLP pipeline

**Key methods:** `allow()`, `record_success()`, `record_failure()`, `status()`

---

## 2. Groq Client Updates — `backend/shared/groq_client.py`

### 2a. Model name migration

**What:** `FAST_MODEL` changed from `llama-3.1-8b-instant` to `openai/gpt-oss-20b`. `SMART_MODEL` changed from `llama-3.3-70b-versatile` to `openai/gpt-oss-120b`.

**Why:** Groq deprecated the Llama models on 2026-08-16. Calls to the old model names return errors. The worker runs every 15 minutes around the clock — dead model names would cause every summarisation and bias analysis call to fail silently, stopping the NLP pipeline from producing summaries or conflict framing. The GPT-OSS models are Groq's official replacements per their deprecation documentation.

### 2b. Circuit breaker wiring

**What:** Every `chat()` call now checks `_groq_cb.allow()` before firing, calls `record_success()` on completion, and `record_failure()` on exception. Added `get_groq_cb_status()` function for health endpoint exposure.

**Why:** The model name fix prevents known errors. The circuit breaker handles unknown future ones — temporary Groq outages, rate limit spikes, network issues between the host and Groq. Without it, a 30-minute outage generates hundreds of failed API calls across multiple cycles with no backoff.

---

## 3. Embedding Dimension Upgrade — `backend/nlp_pipeline/task9_embed.py`

### 3a. 384 → 768 dimensions

**What:** `EMBEDDING_DIM` increased from 384 to 768 using Jina's MRL (Matryoshka Representation Learning) truncation on `jina-embeddings-v3`.

**Why:** Conflict detection works by computing cosine similarity between article embeddings — semantically close articles from editorially opposing sources become candidate pairs for NLP contradiction analysis. At 384 dimensions, the vector space is too compressed: headlines that sound similar but carry genuinely different framings end up closer together than they should be (false positives), while truly contradictory articles that use different vocabulary end up too far apart (false negatives). 768 dimensions preserves enough nuance to distinguish "Israel says ceasefire agreed" from "Hamas says no ceasefire agreed" — same event, meaningfully different claims. MRL truncation achieves this without switching models or retraining.

### 3b. Summaries-only filter

**What:** The WHERE clause in `run_task9()` now requires `summary IS NOT NULL OR summary_ar IS NOT NULL` before an article enters the embedding queue.

**Why:** Previously, articles were embedded using `body_snippet` — the raw first 500 characters from the RSS feed, which frequently contains HTML artifacts, bylines, datelines, and footer boilerplate rather than actual content. This noise degrades embedding quality and creates spurious similarity matches. By waiting for task7_5 to produce a clean Groq-generated summary, embeddings represent the distilled meaning of the article rather than its formatting noise. The tradeoff is a longer pipeline delay before articles reach conflict detection, but the pairs generated are significantly higher quality.

### 3c. Jina circuit breaker

**What:** `_jina_cb = CircuitBreaker('jina', failure_threshold=3, cooldown_s=180)`. Each embedding batch checks `allow()` before calling the Jina API. Added `get_jina_cb_status()` for health endpoint.

**Why:** Same rationale as the Groq circuit breaker — prevents hammering a failing paid API and provides observable state for monitoring.

---

## 4. Scheduler Updates — `scheduler.py`

### 4a. Embedding schema migration

**What:** `run_embedding_migration()` added, called at every worker startup before the first ingestion cycle. Inspects `atttypmod` of `articles.embedding` in `pg_attribute`. If 384-dim: drops column, recreates at 768-dim, rebuilds HNSW cosine index, resets `processed_nlp = FALSE`. If already 768-dim: no-op.

**Why:** The dimension change is a breaking schema change — pgvector cannot store mixed-size vectors in the same column. Without automated migration, deploying the new task9 code against a 384-dim column would cause every embedding write to fail. Running it at startup makes the deploy self-healing without requiring a separate manual Supabase migration step. Resetting `processed_nlp` ensures all existing articles get re-embedded at the new dimension rather than being silently skipped.

### 4b. Rich health endpoint

**What:** The HTTP health server response upgraded from `{"status": "worker running"}` to a JSON payload including `groq_usage` (today's call counts and daily caps per model) and `circuit_breakers` (Groq and Jina CB state snapshots).

**Why:** The original response confirmed the process was alive but nothing about whether it was working. A worker with an open circuit breaker — meaning zero LLM calls going through — looked identical to a healthy one. The enriched response lets you diagnose the worker's actual state from a single HTTP call without SSH access to logs.

---

## 5. Topic Fallback Images — `backend/nlp_pipeline/task6_images.py`

**What:** Added `SOURCE_TOPIC` dict (37 source codes → topic category: conflict / politics / humanitarian / news) and `TOPIC_FALLBACK_IMAGES` dict (topic → stable Unsplash CDN URL). Query now JOINs the sources table to get source code alongside article. When og:image fetch fails, writes the topic-appropriate fallback URL instead of leaving `image_url` empty. WHERE clause extended to backfill existing `''` sentinels.

**Why:** Articles without images render as blank grey boxes in the frontend. Previously, failed og:image fetches (frequent for sources that block datacenter scraping) left `image_url = ''` permanently — no retry, no fallback. Topic-matched fallback images mean every article gets a contextually relevant visual regardless of source behaviour. The empty-string backfill was necessary because thousands of already-ingested articles were stuck with the old sentinel value and would never be retried.

---

## 6. Source Routing Fixes — `backend/ingestion_worker/adapters/rss_adapter.py`

**What:** Several sources rerouted from direct URLs to Google News proxy (`news.google.com/rss/search?q=site:...`). AJA+ disabled. AJA switched from English to Arabic feed.

**Why per source:**

- **F24** (France24 Arabic): `france24.com` blocks Render Frankfurt IPs (HTTP 403). Google News proxy bypasses the block.
- **ARB** (Al Arabiya): `alarabiya.net` blocks Render Frankfurt IPs (HTTP 403). Same fix.
- **WAF** (WAFA): Direct `/rss` endpoint returns 404. Google News indexes WAFA content and exposes it reliably.
- **MAYE** (Al Mayadeen English): Direct feed dead after CMS migration. Google News proxy restores access.
- **SDT** (Sudan Tribune): Direct feed dead after site relaunch. Google News proxy restores access.
- **AJA+** (Al Jazeera Plus): Disabled. Content was fetched via RSSHub bridge on Render free tier; Telegram rate-limits the bridge (HTTP 429) on every cycle, meaning zero articles were landing despite the adapter running. Wastes a concurrency slot. Re-enable when a paid RSSHub or alternative Telegram method is available — see `BACKLOG.md`.
- **AJA** (Al Jazeera Arabic): Switched from English (`aljazeera.net/en`) to Arabic feed (`aljazeera.net/rss/all.xml`). Arabic is platform-primary; the Arabic feed publishes hours ahead of the English edition and covers regional stories that never get translated. Using English was wasting the highest-trust source (0.90) on content already covered by BBC, Reuters, and AP.

---

## 7. New Sources Activated

**What:** HAA, TNA, ASH added to `rss_adapter.py` RSS_SOURCES, `worker.py` adapter list, and `main.py` startup upsert + SOURCE_PROFILE. (MAN also added initially but deactivated post-audit — see Source Audit 2026-08.)

**Why:** Conflict detection quality is directly proportional to editorial diversity — the engine surfaces pairs where sources say different things about the same event. The existing source list was heavy on Western wire services and Arab state media but thin on two perspectives: Israeli-internal (distinct from Israeli-government) and pan-Arab independent.

- **HAA** (Haaretz English, trust 0.70): Israeli left-liberal broadsheet. Regularly contradicts JRP (Jerusalem Post) on the same events — exactly the kind of same-event, opposite-framing pair the NLP pipeline is built to surface.
- **ASH** (Asharq Al-Awsat Arabic, trust 0.65): High-quality pan-Arab daily, Saudi-owned but London-based with strong editorial independence. Editorially distinct from ARB (Al Arabiya) despite similar ownership.
- **TNA** (The New Arab English, trust 0.65): UK-based independent, strong on Syria and Gulf. Fills the English-language pro-Arab independent slot with higher editorial quality than advocacy outlets.

---

## 8. Stale feed_url Corrections — `backend/api_server/main.py`

**What:** Three source rows in the startup upsert had `feed_url` values pointing to dead endpoints that didn't match what `rss_adapter.py` actually fetches.

| Code | Old (dead) | New (actual) |
|---|---|---|
| AP | `feeds.apnews.com/rss/apf-topnews` | Google News proxy |
| MAYE | `almayadeen.net/rss/all.xml` | Google News proxy |
| SDT | `sudantribune.com/feed/` | Google News proxy |

**Why:** The `feed_url` column is the canonical DB record of where content comes from — used for display, debugging, and future tooling. Having it point to dead URLs while the worker silently uses different URLs creates a split-brain where code and database tell different stories. Not a runtime bug (the worker reads from `rss_adapter.py`, not the DB), but a maintenance and debugging hazard for anyone trying to understand the system later.

---

## Files Changed in Phase 1

| File | Type |
|---|---|
| `backend/shared/circuit_breaker.py` | New |
| `backend/shared/groq_client.py` | Modified |
| `backend/nlp_pipeline/task9_embed.py` | Modified |
| `backend/nlp_pipeline/task6_images.py` | Modified |
| `scheduler.py` | Modified |
| `backend/ingestion_worker/adapters/rss_adapter.py` | Modified |
| `backend/ingestion_worker/worker.py` | Modified |
| `backend/api_server/main.py` | Modified |

---

## Known Gaps Carried Forward

- **PCH + IMEMC not in `task6_images.py` SOURCE_TOPIC dict** — articles from these sources get a generic fallback image rather than a topic-specific one. Minor visual gap; fix in next change log.
- **AJA+ ingestion disabled** — Telegram content from AJ Plus Arabic unavailable until a paid RSSHub or alternative is provisioned.
- **task6 SOURCE_TOPIC covers 37 source codes** — any future source additions need a corresponding topic mapping added.

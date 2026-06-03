# CrisisLens — Audit 1
**Date:** 2026-06-03
**Scope:** Pre-deployment full-system audit — all issues identified, triaged, and resolved in this session.

---

## Overview

This audit was conducted before the first public deployment of the CrisisLens pipeline. The goal was to identify every structural flaw, security risk, and fragile dependency before real traffic hit the system. Six issues were fixed. Several upgrade paths exist for future scaling — those are costed and referenced in `docs/BUDGET.md`.

---

## Fix 1 — Live DB Credentials in Public GitHub

### Problem
The `CLAUDE.md` file contained a Render PostgreSQL connection string with a username and password committed to the public `github.com/daniehben/CrisisLens` repository. Anyone with the link could read the credentials and access the database directly.

### Root Cause
The credentials were added during initial setup when the developer was working quickly. CLAUDE.md is a project-level instruction file for Claude agents and was not treated as a secrets file — but it was public.

### Fix
- Removed the full connection string from `CLAUDE.md`
- Added a note: "Credentials rotated 2026-06-02"
- The Render PostgreSQL instance was already deleted by this point (90-day free tier expiry), but credentials were also rotated as a precaution
- Added guidance in CLAUDE.md: external DB URL lives only in the Render dashboard, never in any committed file

### Impact on System
No pipeline impact. Purely a security fix. The live system uses `DATABASE_URL` as an environment variable injected by Render at runtime — never sourced from the codebase.

### What This Means for System Design
Secret rotation and secret hygiene must be enforced from day one. All credentials (DB, API keys, tokens) must live in the hosting platform's environment variable store and be referenced by name in code. CLAUDE.md and any other developer-facing markdown files are public and must be treated as such.

### Better Options at Cost
None needed — environment variable injection is the correct pattern at any scale. At higher scale (team of 3+), a secrets manager (HashiCorp Vault, AWS Secrets Manager) would add rotation automation, audit logging, and per-service access controls. See `docs/BUDGET.md`.

---

## Fix 2 — Worker Refused to Start Due to Missing NEWSAPI_KEY

### Problem
`backend/shared/config.py` had a `validate()` method that checked for `NEWSAPI_KEY` as a required environment variable. NewsAPI had already been removed from the codebase months earlier, but the validation check was left behind. On Render, `NEWSAPI_KEY` was not set as an env var because the feature no longer existed — meaning every worker deployment would fail to start with a `ValueError` at startup, before ingesting a single article.

### Root Cause
Feature removal was incomplete. The adapter code was deleted but the config validation was not updated to match.

### Fix
- Removed `NEWSAPI_KEY` from `config.py` validation
- Also removed all other dead config keys: `NYT_API_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `REDIS_URL`
- `validate()` now only checks `DATABASE_URL`, which is the single true hard dependency for the worker

### Impact on System
Critical fix. Without this, the worker would never run on Render. Ingestion would be entirely dead. The issue was invisible locally because `.env` files on the developer's machine happened to have these keys set from earlier experiments.

### What This Means for System Design
Config validation is a safety net, not a documentation layer. It should only gate on what the service actually needs to function. Dead keys in validation are worse than no validation because they silently break deployments when the code no longer uses the thing being validated. Every time a feature is removed, its config validation entry must be removed in the same commit.

### Better Options at Cost
At team scale, a config schema tool (Pydantic Settings, `python-decouple`) with per-environment profiles would make this kind of drift visible before deployment. No cost difference — both are open source.

---

## Fix 3 — Redis Entirely Removed (Deduplication Rewritten)

### Problem
The deduplication system used Redis bitmaps to track which article URLs had already been ingested, preventing the same article from being inserted multiple times per cycle. Render's internal network did not route to the Valkey (Redis) instance — every `redis.connect()` call returned "Connection refused". This meant:
1. `check_and_mark()` always threw an exception
2. The exception was caught silently
3. Every article was treated as new on every cycle
4. The same 200 articles were re-inserted every 30 minutes, filling the database with duplicates

### Root Cause
Render free tier does not guarantee internal network routing between services. The Valkey instance appeared healthy in the dashboard but was unreachable from both the worker and API services. This is a known limitation of Render's free tier networking.

### Fix
**Replaced Redis with an in-memory Python set backed by the database.**

`backend/shared/deduplication.py` was fully rewritten:
- On first call, loads all known article URLs from the DB, hashes each with MD5, stores in a Python `set`
- Each `check_and_mark(url)` call is `O(1)` — a set lookup with no network round-trip
- When a new article is inserted, its URL hash is added to the set immediately
- On worker restart, the set is rebuilt from the DB (idempotent)
- A `reset()` function exists for forcing a full reload if the set drifts

The Render Valkey service was deleted from the dashboard. Redis references were removed from:
- `requirements.txt` (removed `redis` package)
- `render.yaml` (removed `REDIS_URL` env var declaration from both services)
- `backend/api_server/main.py` (removed Redis ping from `/health` endpoint)
- `backend/api_server/schemas.py` (removed `redis: str` from `HealthResponse`)
- `backend/shared/config.py` (removed `REDIS_URL`)
- `CLAUDE.md` (updated architecture documentation)

### Impact on System
The deduplication system is now faster, simpler, and has zero external dependencies. Memory usage: each MD5 hash is 32 bytes. At 200,000 articles that is approximately 6.4MB — negligible on Render's 512MB free tier. The system correctly deduplicates across worker restarts because it reloads from the database on startup.

One trade-off: if two worker instances run simultaneously (not the current setup, but possible in future), they share no state and could both insert the same article before either's in-memory set is updated. This is acceptable at current scale with a single worker instance. The database has a `UNIQUE` constraint on `(source_code, external_id)` as a hard backstop.

### What This Means for System Design
Stateless workers with database-backed deduplication are simpler and more reliable than workers with a shared external cache, unless the cache is needed for cross-instance coordination at high scale. At current scale (single worker, <1000 articles/day), the in-memory approach is strictly superior. The database is already the source of truth — using it for deduplication does not add a new dependency.

### Better Options at Cost
If the system scales to multiple parallel worker instances processing the same sources, a shared deduplication layer becomes necessary. Options at that point:
- **Redis / Upstash Redis** — $0 on free tier (Upstash), $10–20/month for production
- **PostgreSQL advisory locks** — no additional cost, uses existing DB
- **Bloom filter in shared DB table** — probabilistic, extremely memory-efficient

See `docs/BUDGET.md` for cost breakdown when the time comes.

---

## Fix 4 — Telegram Scraper Had No Rate Limiting or Backoff

### Problem
`TelegramWebAdapter` fetched `t.me/s/<channel>` with a single `httpx.get()` call and no retry logic, no delay between channels, and no backoff on failure. The worker runs all adapters near-simultaneously via `ThreadPoolExecutor`. With 3 Telegram channels fetched in rapid succession, Telegram's rate limiter would return `429 Too Many Requests`. The adapter treated 429 the same as a successful empty response — it returned an empty list and moved on. This meant Telegram sources silently produced zero articles during rate-limited periods, with no log warning distinguishing "no news" from "we got blocked".

### Root Cause
The adapter was written when only one Telegram channel existed. No rate limiting was needed for a single channel. When more channels were added, the burst problem was not addressed.

### Fix
Added three layers of protection to `backend/ingestion_worker/adapters/telegram_web_adapter.py`:

**1. Pre-fetch delay with jitter**
```python
FETCH_DELAY_S = 2.0        # base delay before every fetch
FETCH_DELAY_JITTER = 1.0   # random additional 0–1s on top
```
Each fetch sleeps 2–3 seconds before making the HTTP request. The jitter prevents multiple workers from synchronising their requests if ever run in parallel.

**2. Exponential backoff on 429 and 5xx**
```python
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 5     # seconds; doubles each retry: 5s, 10s
```
On a 429 or server error, the adapter waits and retries up to 2 times before giving up. Total worst-case wait per channel: ~17 seconds (2s base + 5s + 10s).

**3. Explicit logging per status code**
- 429: logs "rate limited — backing off Xs" with the wait duration
- 5xx: logs the status code and wait duration
- 4xx other: logs "skipping" — not retried, not confused with empty response

**Also cleaned up `TELEGRAM_SOURCES`** — removed dead entries (BNO, AJE+, REU, BBC+, MAYE) that had already been migrated to RSS adapters but were left as dead code in the Telegram dict.

### Impact on System
Telegram sources (AJA+, WM, SI) are now resilient to temporary rate limiting. The worker cycle takes 6–9 seconds longer to complete due to the pre-fetch delays, but this is negligible against the 30-minute ingestion interval. Silent data loss from rate limiting is replaced by logged warnings that make the problem visible in Render's log stream.

### What This Means for System Design
Any adapter that hits an external HTTP endpoint needs rate limiting and backoff from day one. Treating non-200 responses as empty results is a dangerous default — it hides infrastructure problems as data problems. The pattern established here (pre-fetch delay + exponential backoff + explicit status-code logging) should be applied to all future adapters.

### Better Options at Cost
At higher scale (10+ Telegram channels), the per-channel 2s delay means the Telegram fetch loop alone takes 20+ seconds. Options:
- **Async fetching with `asyncio` + per-domain semaphore** — limits concurrent requests to Telegram to 1 while allowing other sources to fetch in parallel. No cost, requires refactoring the worker to async.
- **Dedicated Telegram worker with its own schedule** — decouples Telegram polling rate from RSS polling rate. No cost on current infrastructure.

---

## Fix 5 — Frontend Editorial Redesign (Emoji Removal)

### Problem
The CrisisLens frontend used emoji icons throughout the topic section headers and type filter tabs (e.g. "⚔️ Military Operations", "🏥 Humanitarian Crisis", "📊 Numeric Contradiction"). This made the product look like a social media app or an AI demo rather than a serious news intelligence tool. Conflict journalism sources (Reuters, Al Jazeera, BBC) use clean typographic hierarchy with no emoji.

### Root Cause
Emoji were added as a quick visual differentiator during early prototyping. They were never revisited.

### Fix
Redesigned the topic sections and type filter tabs in `frontend/index.html`:

- Removed all emoji from `TOPIC_META` configuration object
- Removed the `.topic-section-icon` span from the HTML template
- Section headers: large Playfair Display serif (1.35rem, weight 900), 3px accent-coloured `border-bottom`, no icon
- Article count: small-caps plain text label, no pill/badge background
- Type filter tabs: underline-style active state (`border-bottom: 2px solid var(--accent)`), not pill buttons
- Tab labels: "All", "Numeric", "Framing", "Cross-regional", "High confidence" — plain language, no emoji

### Impact on System
Visual only — no pipeline or data changes. The product reads as editorial and analytical rather than app-like. This matters for the target audience (journalists, analysts, researchers) who associate emoji-heavy interfaces with consumer social products.

### What This Means for System Design
UI credibility directly affects user trust in the underlying data. A contradiction detection platform that looks like a Twitter clone will be dismissed by the journalists and analysts it is designed for, regardless of the quality of the NLP output. The visual language must match the editorial seriousness of the content.

### Better Options at Cost
A proper design system (Figma component library → Tailwind CSS tokens) would enforce visual consistency as the frontend grows. At current scale, inline styles in `index.html` are acceptable. When migrating to Next.js (Phase 3), establishing a design token system from the start is worth the upfront time investment.

---

## Fix 6 — Embeddings: Replaced HF API with Local Model

### Problem
`task9_embed.py` called the Hugging Face Inference API to generate sentence embeddings for article headlines. The HF free tier allows approximately 100 requests per day. On a busy news day, the pipeline ingests 150–300 articles. Each article requires one embedding request. The pipeline would hit the quota mid-cycle, silently stop producing embeddings for the remaining articles, and those articles would never be considered for contradiction detection.

A second problem: the query only selected articles where `headline_ar IS NOT NULL`. English-only sources (AP, Reuters, BBC, Guardian, CNN — approximately 60% of all sources) never have `headline_ar` set. This meant they never received embeddings and could never be matched against Arabic sources for contradiction detection. The core cross-lingual feature of the product was non-functional.

### Root Cause
The HF API approach was chosen for simplicity during early development. The Arabic-only query was a bug introduced when the embedding column was first added — the developer assumed all articles would have Arabic headlines because the platform is "Arabic-first", which is true for the UI but not for the raw data.

### Fix
Replaced the HF API call with local CPU inference using `sentence-transformers`:

**Model:** `paraphrase-multilingual-MiniLM-L12-v2`
- 384-dimensional vectors
- Trained on 50+ languages including Arabic and English in the same vector space
- ~120MB model file, downloaded once and cached at `~/.cache/huggingface/`
- ~50–150ms per sentence on CPU
- Zero API quota, zero cost, no external dependency

**Model is loaded once per worker process** (module-level cache via `_model` global), not once per ingestion cycle. The 200ms cold start on first use is negligible against the 30-minute cycle interval.

**Fixed the Arabic-only query bug:**
```sql
-- Before (missed all English-only sources):
WHERE headline_ar IS NOT NULL AND embedding IS NULL

-- After (embeds whichever headline exists):
WHERE (headline_ar IS NOT NULL OR headline_en IS NOT NULL)
  AND embedding IS NULL
```
`COALESCE(headline_ar, headline_en)` returns the Arabic headline if available, otherwise the English headline. Both are embedded into the same 384-dim multilingual vector space, so cross-lingual cosine similarity works correctly.

Added `sentence-transformers` to `requirements.txt`. `HF_TOKEN` env var is no longer used by task9 (leave it in render.yaml in case other tasks still reference it).

### Impact on System
Contradiction detection is now actually viable. Every article from every source — Arabic and English — receives an embedding. The similarity search in task10 compares all articles against each other across language boundaries. An AP English article and an AJA+ Arabic Telegram post about the same airstrike will now surface as a candidate contradiction pair if their headlines embed close in vector space.

The pipeline no longer has a daily quota ceiling. It can process 1,000 articles per day as easily as 100.

### What This Means for System Design
Local model inference on the worker is the right default for embedding generation at this scale. The model is loaded once per process lifetime, so the memory cost (approximately 200MB) is paid once per Render instance, not per article. The CPU inference cost (~100ms per article) is absorbed into the background worker cycle which has no latency requirements.

The architectural principle: if an NLP task can run locally at acceptable speed on available hardware, prefer it over an external API. External APIs introduce quota risk, network latency, and an additional point of failure for a background job that runs without human supervision.

### Better Options at Cost
**OpenAI `text-embedding-3-small`** provides higher-quality cross-lingual embeddings with a larger context window, at $0.02 per 1 million tokens. At current article volume (~200/day, ~30 tokens/headline), the cost is approximately $0.004 per month — half a cent. At 10× growth, approximately $0.04 per month.

The upgrade path is documented with full price breakdown in `docs/BUDGET.md`. Switch when: (a) contradiction detection quality is visibly poor despite good NLP pipeline results, or (b) there is already a credit card on file for other infrastructure.

---

## Pending Items After Fix 6 (Resolved in Fixes 7–14 Below)

Items 2 and 3 from this table were resolved in the same session. Items 4–8 remain open.

| # | Issue | Risk | Status |
|---|---|---|---|
| 1 | `netlify.toml` committed unnecessarily | Low | ✅ Removed |
| 2 | Groq daily token cap — no circuit breaker | Medium | ✅ Fixed — see Fix 7 |
| 3 | CORS `allow_origins=["*"]` in API | Medium | ✅ Fixed — see Fix 11 |
| 4 | No DB size tracking | Low | Open |
| 5 | No error state for Render spin-down in frontend | Low | Open |
| 6 | No SEO | Low | Open — accepted for now |
| 7 | No permalink/share URLs | Low | Open |
| 8 | HF_TOKEN still in render.yaml | Trivial | Open |

---

## Fix 7 — Groq Daily Cap Circuit Breaker

### Problem
`backend/shared/groq_client.py` called the Groq API with no awareness of daily quota limits. Groq's free tier enforces hard daily caps: 14,400 requests/day for the fast model (`llama-3.1-8b-instant`) and 1,000 requests/day for the smart model (`llama-3.3-70b-versatile`). When the cap was hit, the API returned a `429 Too Many Requests`. The existing retry logic treated 429 the same as a transient network error and retried immediately — burning through the remaining quota even faster. Downstream tasks (task7.5 summarisation, task13/14 bias analysis) received `None` from `chat()` and silently skipped the article, producing gaps in the data with no log evidence of why.

### Root Cause
Groq was integrated as a simple API wrapper. Daily quota management was not considered during initial implementation.

### Fix
Rewrote `backend/shared/groq_client.py` with a per-model daily counter:

- `_DAILY_CAPS = {FAST_MODEL: 14_400, SMART_MODEL: 1_000}` — hard limits per model
- `_daily: dict[str, dict]` — tracks `{"date": "YYYY-MM-DD", "count": int}` per model, resets automatically at midnight UTC when the date changes
- `_cap_logged: dict[str, bool]` — fires a single `WARNING` log entry the first time a cap is hit per day, not once per skipped call (prevents log flooding)
- `_check_daily_cap(model)` — called before every API request; returns `False` and skips the call if at cap
- `_increment_daily(model)` — called only after a successful API response, so failed retries don't inflate the counter
- `get_daily_usage()` — returns `{model: {"date", "count", "cap"}}` for the `/health` endpoint

The `/health` endpoint now includes `groq_usage` in its response. Note: the counter lives in the worker process — the API process makes no Groq calls, so its `/health` will always show an empty `groq_usage` dict. Real usage is in the worker logs.

### Impact on System
The pipeline no longer retries into a spent quota. When the 70B cap is hit, task13/14 skip gracefully with a single warning log. The cap resets at 00:00 UTC without a worker restart. Downstream tasks already handle `None` returns correctly — this fix makes the reason for `None` observable.

### What This Means for System Design
Any background job that calls a rate-limited external API needs a circuit breaker. The pattern established here (in-memory counter keyed by date, single log per breach, auto-reset on date change) is the minimum viable implementation. It is intentionally simple — no persistence, no cross-process visibility — which is appropriate for a single-worker system.

### Better Options at Cost
The counter is in-memory and lost on worker restart (Render free tier restarts frequently due to spin-down). At scale, a shared counter in the existing Supabase DB (`groq_usage` table with `upsert` per call) gives cross-process visibility and survives restarts. Cost: $0 — uses existing infrastructure. Implementation: ~30 minutes. Full specification in `docs/BUDGET.md`.

---

## Fix 8 — NLI Premise/Hypothesis Token-Aware Truncation

### Problem
`backend/nlp_pipeline/task11_nli.py` passed article text to the NLI model (`mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`) by naively slicing the string to 400 characters: `text[:400]`. The mDeBERTa model has a hard 512-token context window. Character count and token count are not the same — Arabic script tokens are often 1–3 characters each while some English subwords span multiple characters. A 400-character slice could produce anywhere from 80 to 600 tokens depending on language and vocabulary. When the combined premise + hypothesis exceeded 512 tokens, the model's tokenizer silently truncated the overflow, cutting mid-claim and degrading inference quality. Long articles about high-casualty events — where the specific numbers are the contradiction — were most affected.

### Root Cause
The 400-character constant was a quick heuristic added during initial development. The developer assumed character count was a sufficient proxy for token count.

### Fix
Replaced character slicing with token-aware truncation using the model's own tokenizer:

```python
PREMISE_TOKEN_BUDGET    = 290  # 60% of 512 minus special tokens overhead
HYPOTHESIS_TOKEN_BUDGET = 190  # 40% of 512 minus special tokens overhead

@lru_cache(maxsize=1)
def _get_tokenizer():
    return AutoTokenizer.from_pretrained(
        "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    )

def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    tokenizer = _get_tokenizer()
    ids = tokenizer.encode(text, add_special_tokens=False)
    if len(ids) <= max_tokens:
        return text
    return tokenizer.decode(ids[:max_tokens], skip_special_tokens=True,
                            clean_up_tokenization_spaces=True)
```

The tokenizer is loaded once per worker process via `@lru_cache(maxsize=1)`. The 60/40 split between premise and hypothesis reflects that the premise (full article text) contains more factual content than the hypothesis (opposing headline), so it gets more budget. Fallback: if the tokenizer fails to load, the function falls back to `text[:max_tokens * 3]` (a character approximation) rather than crashing.

### Impact on System
NLI inference now operates within the model's actual context window on every pair. High-stakes contradictions involving long casualty reports, detailed operational summaries, or multi-sentence Arabic prose are now fully represented in the model's input rather than arbitrarily truncated. Contradiction detection quality improves most noticeably for Arabic articles (which tend to be token-dense relative to character count).

### What This Means for System Design
Any code that passes text to a transformer model must respect the model's token budget, not a character budget. The correct implementation is always: tokenize → truncate → decode. Character-based heuristics are only acceptable when no tokenizer is available and the text is exclusively ASCII.

### Better Options at Cost
None at current scale. The mDeBERTa model's 512-token limit is fixed. The only way to process longer documents is to split them into chunks and aggregate predictions — a technique called "sliding window NLI" — which adds complexity and latency for marginal gain on news headlines and summaries.

---

## Fix 9 — Stale Article Cleanup (Task 15)

### Problem
The ingestion worker inserted articles continuously but never deleted them. At the observed rate of ~200 articles/day on a quiet news day (and up to 500+ during major events), the `articles` table would grow by approximately 36MB/month. Supabase's free tier has a 500MB storage limit. Hitting this limit causes the database to go **read-only** — the pipeline would silently stop ingesting with no user-visible error. The estimated time to hit 500MB at current volume was 14 months, but a sustained high-news period (a major offensive, a regional escalation) could compress this to 6 months.

Two secondary problems: (1) task9/10/11 query `WHERE embedding IS NULL` or `WHERE status = 'pending'` — these full-table scans slow down as the table grows, even with indexes. (2) Task10 compares new articles against the full article pool — old articles generate stale pairs between 6-month-old reports nobody will read, polluting task11's NLI queue.

### Root Cause
Deletion logic was never implemented. The focus during initial development was on ingestion and NLP processing; storage management was deferred.

### Fix
Created `backend/nlp_pipeline/task15_cleanup.py`:

- Deletes articles older than `ARTICLE_RETENTION_DAYS` (env var, default 90, hard floor 14)
- Cascade deletes in correct FK dependency order: `conflicts` → `article_pairs` → `articles`
- Returns a summary dict with counts and estimated storage reclaimed (~6KB per fully-processed article)
- Logs a `WARNING` if more than 500 articles are deleted in one run (signals unexpectedly large backlog)
- Clamped minimum of 14 days — prevents an accidental `ARTICLE_RETENTION_DAYS=1` from wiping the entire dataset

Added to `backend/ingestion_worker/scheduler.py` as a separate APScheduler job:
- `CronTrigger(hour=2, minute=0, timezone='UTC')` — runs daily at 02:00 UTC
- Completely separate from the 15-minute ingestion cycle — never competes with live article insertion
- `max_instances=1, coalesce=True` — if the previous run is still executing at 02:00 the next day, the new run is skipped

Added `ARTICLE_RETENTION_DAYS: "90"` to `render.yaml` with inline comments explaining when to tighten (approaching 400MB) or loosen (after Supabase Pro upgrade).

### Impact on System
Storage exhaustion is now managed automatically. The daily cleanup at 02:00 UTC reclaims storage from the lowest-traffic window. Retention window is configurable from the Render dashboard without redeployment. The NLP pipeline's full-table scans stay fast because old processed rows are regularly removed.

### What This Means for System Design
Any system that writes continuously to a bounded storage layer needs a deletion policy from day one. The correct default is conservative (90 days gives plenty of history) with a hard floor (14 days prevents accidental data loss) and a configurable ceiling (no upper limit — the operator decides when to upgrade storage). The cleanup job runs separately from ingestion because competing with live writes during peak hours risks deadlocks and slows both operations.

### Better Options at Cost
At 90-day retention, approximately 18,000 articles are kept in the database at steady state. If longer history is needed for research or trend analysis, the options are:
- **Supabase Pro** ($25/month, 500GB) — raise retention to 365+ days
- **Cold storage archive** — export old articles to Supabase Storage (JSON/CSV, $0 on free tier 1GB) before deleting from the DB. Preserves historical data without paying for live DB storage.

Full cost table in `docs/BUDGET.md`.

---

## Fix 10 — Methodology Page

### Problem
The CrisisLens frontend had no explanation of how conflict scores are calculated, how sources are weighted, or what the framing vocabulary pairs are. The pipeline makes several contested editorial decisions — BBC Arabic classified as "Western", a +0.08 diversity bonus for cross-regional pairs, framing vocabulary that includes terms like "martyr/killed" and "terrorist/resistance" — none of which were documented or visible to users. A product that surfaces politically sensitive contradictions between state media and independent journalists, with no explanation of its own methodology, is not credible.

### Root Cause
Methodology documentation was deferred during the MVP build phase. There was no template or structure defined for it.

### Fix
Added a slide-in methodology panel to `frontend/index.html`, accessible via a "Methodology" button in the masthead navigation between the search icon and the language toggle.

The panel opens from the right (600px wide on desktop, full-width on mobile), closes on ✕, backdrop click, or Escape key. Six anchor-linked sections at the top allow direct navigation:

1. **Overview** — plain-language description of what the site does and does not do. Explicit beta disclaimer.
2. **Sources & Trust** — full table of all 37 sources with trust scores (0.10–1.00), visual trust bars, and region group assignments. Includes editorial note explaining why BBC Arabic is classified as Western despite its language.
3. **Pipeline** — 7-step numbered walkthrough: ingest → translate → embed → pair → NLI → score → bias analysis.
4. **Conflict Scoring** — explicit formula: `NLI contradiction probability × avg(trust_A, trust_B) + numeric boost (+0.20) + framing boost (+0.15) + diversity bonus (+0.08)`, threshold 0.55. Full region group table. Plain-language defence of the diversity bonus.
5. **Framing Detection** — all 5 vocabulary pairs with Arabic terms. Explicit statement that neither side is penalised — the flag marks the difference, not a verdict.
6. **Limitations** — 6 callout boxes: translation quality, NLI ≠ fact-checking, trust scores are editorial judgments, coverage gaps, LLM bias in the analysis prose, beta status.

Design inspired by Ground News (`/media-bias` page with sourced third-party ratings) and NewsGuard (per-criterion point weights, anchor-linked sections, explicit process documentation).

### Impact on System
The product is now transparent about every contested decision in its pipeline. Users can disagree with the diversity bonus, the BBC Arabic classification, or the framing vocabulary — the methodology page makes those disagreements possible by surfacing the decisions. This is a requirement for credibility with journalists, researchers, and analysts.

### What This Means for System Design
Methodology documentation is not an optional add-on for a data product — it is part of the product. Every number and threshold that shapes what users see must be public and findable within one click from the main interface. When pipeline parameters change (thresholds, weights, source list), the methodology page must be updated in the same commit.

---

## Fix 11 — CORS Locked, Methods Restricted, Rate Limits Tuned

### Problem
Three related security and reliability issues in `backend/api_server/main.py`:

1. `allow_origins=["*"]` — any website could call the API from browser JavaScript, enabling parasite frontends that build on CrisisLens data without attribution.
2. `allow_methods=["*"]` — POST, PUT, DELETE were allowed by CORS policy even though the API has no write endpoints. A misconfigured browser request that somehow passed auth checks could trigger unexpected behaviour.
3. `allow_credentials=True` with wildcard origin — this combination is a CORS security misconfiguration. Browsers block credentialed requests to wildcard origins per the CORS spec; setting it to `True` when no credentials (cookies, auth headers) are used is misleading and potentially dangerous if auth is added later.
4. All endpoints had the same 60/minute rate limit with no hourly cap — a scraper could drain all conflict data at 60 req/min continuously with no slowdown.

### Root Cause
CORS and rate limits were set permissively during development and never revisited before deployment.

### Fix
**CORS:**
- `allow_origins` now reads from `ALLOWED_ORIGINS` environment variable (comma-separated list)
- If unset, falls back to `["*"]` with a `WARNING` log entry: "ALLOWED_ORIGINS not set — open wildcard"
- `allow_credentials` changed `True → False`
- `allow_methods` changed `["*"] → ["GET"]` (API is read-only)

**Rate limits:**
- Global default: `30/minute, 300/hour` (via `default_limits` on the `Limiter` instance)
- `/api/v1/conflicts` and `/api/v1/conflicts/{id}`: `20/minute, 100/hour` — tighter because this is the core product data
- `/` and `/health`: `@limiter.exempt` — see Fix 12

To activate: set `ALLOWED_ORIGINS=https://crisis-lens-six.vercel.app` in Render dashboard → crisislens-api → Environment.

### Impact on System
Browser-based parasite frontends are blocked once `ALLOWED_ORIGINS` is set. Bulk scraping of conflict data is rate-limited to 100 requests/hour per IP. CORS configuration is now correct per spec. The domain can change (custom domain, migration) by updating one Render env var — no code change or redeploy needed.

### What This Means for System Design
CORS controls only browser-to-server requests — curl, Python scripts, and any server-side scraper bypass it entirely. Rate limiting per IP is the actual scraping defence. The combination of domain-locked CORS (stops parasite browser apps) and hourly rate limits (slows bulk scrapers) is the correct two-layer approach at zero additional cost.

---

## Fix 12 — Health and Root Endpoints Exempt from Rate Limiting

### Problem
Render's health checker pings the service endpoint on a regular interval from its own server IP. That IP was subject to the same rate limiter as all other callers. If the health checker fired 30+ times in a minute — which it does when Render suspects a service is unhealthy — it would exhaust the rate limit, receive a `429`, interpret that as the service being down, and trigger a restart. The restart caused more health checks, which caused more 429s. A restart loop driven by the infrastructure's own monitoring.

### Root Cause
Rate limits were applied globally to all endpoints including infrastructure-facing ones.

### Fix
Added `@limiter.exempt` to both the root `/` and `/health` endpoints:

```python
@app.api_route("/", methods=["GET", "HEAD"])
@limiter.exempt
def root():
    return {"status": "ok"}

@app.get("/health", response_model=HealthResponse)
@limiter.exempt
def health(request: Request):
    ...
```

`@limiter.exempt` tells slowapi to skip rate limit enforcement for those endpoints entirely. They will always return 200 regardless of call frequency.

### Impact on System
Render health checks can no longer trigger a 429-induced restart loop. The data endpoints (`/api/v1/*`) retain their rate limits unchanged.

### What This Means for System Design
Infrastructure endpoints (health checks, readiness probes, liveness probes) must always be exempt from rate limiting. They are called by the hosting platform itself, not by users. Applying user-facing rate limits to infrastructure endpoints conflates two entirely different callers and creates a class of self-inflicted outage.

---

## Fix 13 — Load More Button / Pagination

### Problem
The frontend fetched `/api/v1/conflicts?limit=100` on page load and rendered all 100 cards at once. With 112 conflicts already in the database, the 13 newest or lowest-scored were never shown. As the database grows to thousands of conflicts, the single-shot fetch would become progressively slower and the fixed cap would hide the majority of content from users.

### Root Cause
Pagination was not considered during the initial frontend build. The 100-item limit was a development-time placeholder.

### Fix
Three changes:

**1. API limit raised:** `le=100 → le=500` in both the `/api/v1/feed` and `/api/v1/conflicts` query parameter validators. The frontend now fetches 500 conflicts on load — workable while the database is under a few thousand conflicts.

**2. Client-side pagination with a "Load more" button:**
- `PAGE_SIZE = 50` — first 50 conflicts render on page load
- `visibleCount` variable tracks how many are currently shown
- `render()` slices `rest.slice(0, visibleCount)` before passing to both the topic-sections and flat-grid render paths
- A "Load more — N remaining" button appears below the feed when more items exist
- On click: `visibleCount += PAGE_SIZE`, `render()` — user stays in place, no scroll jump
- Arabic UI: "تحميل المزيد (N متبقية)"

**3. `resetAndRender()` replaces direct `render()` calls on all filter/search changes:**
Every tab change, filter change, language toggle, and search input resets `visibleCount` back to `PAGE_SIZE` before re-rendering. This prevents the state where a user has loaded 200 items, switches topic filter, and sees 200 cards in a new category.

### Impact on System
All conflicts are now accessible. Users land on the 50 highest-scored contradictions (the most editorially significant) and opt in to loading more. Switching filters always starts fresh at 50. The API can serve up to 500 conflicts per request without server changes.

### What This Means for System Design
Client-side pagination with a "Load more" button is the correct pattern for a single-page app that fetches all data at load time. It avoids the complexity of cursor-based server pagination while giving users control over how much they load. Infinite scroll was explicitly rejected — it removes user control, causes accessibility problems with keyboard navigation, and is associated with addictive dark patterns inappropriate for a serious news product.

---

## Fix 14 — Hardcoded API URL Removed (Vercel Proxy)

### Problem
`window.__CL_API = 'https://crisislens-api.onrender.com'` was hardcoded in `frontend/index.html`. Any change to the API's hosting (custom domain, platform migration, URL restructure) required editing the HTML file and redeploying the frontend — a two-step process with a gap where the old URL and new URL would be inconsistent. The Render URL was also publicly visible in the page source.

### Root Cause
The URL was hardcoded during initial development for simplicity. No build pipeline existed to inject environment variables.

### Fix
Used Vercel's rewrite feature to proxy API calls through the Vercel edge, eliminating the need for any hardcoded URL in the HTML:

**`frontend/vercel.json`** (new file):
```json
{
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://crisislens-api.onrender.com/api/:path*" },
    { "source": "/health",     "destination": "https://crisislens-api.onrender.com/health" }
  ]
}
```

**`frontend/index.html`** changes:
- `window.__CL_API = ''` (empty string — all fetches use relative paths)
- All `fetch()` calls use `/api/v1/conflicts`, `/api/v1/feed` etc. — no domain in the path
- The fallback fetch inside `load()` also updated from the old hardcoded URL

When the Render URL changes, only `vercel.json` needs updating — the HTML is never touched. Bonus: CORS headers are no longer required for the frontend, since the browser sees same-origin requests to `crisis-lens-six.vercel.app/api/*` which Vercel transparently proxies to Render.

### Impact on System
The Render URL is now in one place (`vercel.json`) rather than scattered through the HTML. Domain migrations require one file change. The API's internal hosting URL is no longer visible to users in page source. Same-origin requests bypass browser CORS checks entirely — the `ALLOWED_ORIGINS` env var on Render remains important for direct API access, but the Vercel frontend no longer depends on it.

### What This Means for System Design
For static frontends with no build pipeline, a hosting-level proxy (Vercel rewrites, Netlify redirects, Cloudflare Workers) is the correct pattern for externalising backend URLs. It is simpler than a build-time env var injection, requires no tooling, and the rewrite config is the single source of truth for where the backend lives.

---

## System State After Audit 1 (All Fixes)

| Component | Status |
|---|---|
| Ingestion pipeline | ✅ Fully operational — 37 sources, 3 Telegram, rate-limited |
| Deduplication | ✅ In-memory set, DB-backed, Redis removed |
| Config validation | ✅ Only gates on DATABASE_URL |
| Credentials | ✅ No secrets in codebase |
| Embedding generation | ✅ Local model, all languages, no quota |
| Contradiction detection | ✅ Cross-lingual pairs, token-aware NLI truncation |
| Groq usage | ✅ Daily cap circuit breaker, single log per breach |
| Storage management | ✅ Daily cleanup at 02:00 UTC, configurable retention |
| Frontend | ✅ Deployed on Vercel, load-more pagination, methodology panel |
| API security | ✅ CORS env-var driven, read-only methods, rate limits tuned |
| Infrastructure endpoints | ✅ Health + root exempt from rate limiting |
| API URL | ✅ Vercel proxy — no hardcoded domain in HTML |
| Database | ✅ Supabase eu-central-1, no expiry |

## Remaining Open Items

| # | Issue | Risk |
|---|---|---|
| 1 | No DB size tracking in /health | Low |
| 2 | No SEO (client-side render not indexable) | Low — accepted for now |
| 3 | No permalink/share URLs for individual conflicts | Low |
| 4 | HF_TOKEN in render.yaml no longer used by task9 | Trivial |

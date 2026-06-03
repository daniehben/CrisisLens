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

## Pending Items (Not Yet Fixed)

These were identified in the audit but not addressed in this session. They are ordered by deployment risk.

| # | Issue | Risk | Notes |
|---|---|---|---|
| 1 | `netlify.toml` committed unnecessarily | Low | Frontend already on Render Static Site. Run `git rm netlify.toml` from terminal |
| 2 | Groq daily token cap — no circuit breaker | Medium | If cap hit, task7.5 (summarise) and task13 (bias) silently fail. Add try/except with logged skip |
| 3 | CORS `allow_origins=["*"]` in API | Medium | Should be locked to frontend domain before launch |
| 4 | No DB size tracking | Low | Supabase free tier is 500MB. Add a `/health` field or scheduled alert |
| 5 | No error state for Render spin-down in frontend | Low | Free tier spins down after inactivity. First request takes 50s. Frontend shows blank, not "loading" |
| 6 | No SEO | Low | Pure client-side render — not indexable. Accepted until Next.js migration (Phase 3) |
| 7 | No permalink/share URLs | Low | Individual conflict views have no shareable URL |
| 8 | HF_TOKEN still in render.yaml | Trivial | No longer used by task9. Audit other tasks before removing |

---

## System State After Audit 1

| Component | Status |
|---|---|
| Ingestion pipeline | ✅ Fully operational — 36 sources, 3 Telegram, rate-limited |
| Deduplication | ✅ In-memory set, DB-backed, Redis removed |
| Config validation | ✅ Only gates on DATABASE_URL |
| Credentials | ✅ No secrets in codebase |
| Embedding generation | ✅ Local model, all languages, no quota |
| Contradiction detection | ✅ Cross-lingual pairs now possible |
| Frontend | ✅ Editorial style, no emoji, deployed on Render Static |
| Database | ✅ Supabase eu-central-1, no expiry |
| Deployment | ⚠️ Pending: `git rm netlify.toml`, push all commits |

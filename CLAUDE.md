# CrisisLens — CLAUDE.md

## Project Overview
Real-time Arabic-first conflict news aggregation platform. Zero budget, free-tier infrastructure.

## Infrastructure
- **GitHub:** github.com/daniehben/CrisisLens (main branch)
- **Render PostgreSQL:** crisislens-db, Frankfurt free tier
  - Internal URL (worker/API on Render): set as `DATABASE_URL` env var in Render dashboard — **never commit here**
  - **External URL (from your Mac):** Render dashboard → crisislens-db → Connect → External Database URL
  - ⚠️ **Credentials were accidentally committed prior to 2026-06-02 — rotated on that date**
  - ⚠️ **Never use psql from Anaconda env** — Anaconda's OpenSSL breaks Render SSL.
  - ⚠️ **Never use `python migrate.py` locally** — asyncpg AND psycopg2 both fail on Anaconda macOS (SSL stack incompatibility).
  - ⚠️ **Render Shell requires paid plan** — not available on free tier.
  - **✅ Canonical migration workflow (zero local tooling needed):**
    1. Add idempotent SQL to `run_startup_migrations()` in `backend/api_server/main.py`
       - Use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for schema changes
       - Use `INSERT INTO ... ON CONFLICT (code) DO UPDATE SET ...` for source rows
    2. `git push origin main` — Render redeploys the API, startup migrations run automatically
    3. The API uses the **internal** DB URL (no SSL issues, no external access needed)
  - **Never need to run DB migrations manually** — always embed them in `run_startup_migrations()` and push
- **Redis:** Removed entirely (2026-06-02). Was unreachable from Render internal network anyway.
  Deduplication now uses an in-memory URL hash set in `backend/shared/deduplication.py` — loads all known URLs from DB on startup, O(1) lookups, no external dependency.
- **Render Web Services** (Frankfurt):
  - crisislens-api: https://crisislens-api.onrender.com — LIVE ✅ (free tier)
  - crisislens-worker: https://crisislens-worker.onrender.com — ⏸ SUSPENDED (migrated to Railway)
- **Railway** (worker, migrated 2026-06-06):
  - crisislens-worker: https://web-production-f03a4.up.railway.app — LIVE ✅
  - Trial credit: $4.92 / 28 days (expires ~2026-07-04). Upgrade to Hobby ($5/mo) before expiry.
  - Required env vars: `DATABASE_URL`, `GROQ_API_KEY`, `HF_TOKEN`. HuggingFace now requires a token even for public model downloads (task9 embedding model). `HF_TOKEN` is no longer needed for task11 (migrated to Groq) but IS still required for task9.
- **Supabase (PostgreSQL host):**
  - ⚠️ **Direct connection URL (`db.xxxx.supabase.co:5432`) resolves to IPv6 — Render and Railway cannot reach it.**
  - ✅ **Always use the Session Pooler URL** (`aws-0-eu-central-1.pooler.supabase.com:6543`) — this is IPv4 compatible.
  - Find it in: Supabase dashboard → Project → Connect → Session Pooler

## DATABASE_URL Normalisation (database.py)
`backend/shared/database.py` auto-normalises common URL variants before passing to psycopg2:
- `postgres://...` → `postgresql://...` (Supabase pooler default scheme)
- `postgresql:/user...` → `postgresql://user...` (single-slash copy-paste typo)

If you see `invalid dsn` errors in logs, check the URL format in the relevant service's env vars.

## Render Deployment Notes (CRITICAL)
- Both services must be `type: web` in render.yaml — `type: worker` has no HTTP server and times out
- Health check path must be EMPTY in Render settings — do NOT set it to `/health`
- Root endpoint must accept HEAD requests: `@app.api_route("/", methods=["GET", "HEAD"])`
- Add `PYTHONUNBUFFERED=1` env var to worker for visible logs
- Free tier spins down after inactivity — first request takes 50+ seconds

## Active Sources
| Code | Name | Type | Language | Trust | Status |
|------|------|------|----------|-------|--------|
| AJA | Al Jazeera | RSS | en | 1.00 | ✅ Live |
| BBC | BBC News | RSS | en | 0.80 | ✅ Live (feeds.bbci.co.uk) |
| REU | Reuters | RSS | en | 0.85 | ✅ Live (Google News proxy) |
| AP | Associated Press | RSS | en | 0.80 | ✅ Live (feeds.apnews.com) |
| WP | Washington Post | RSS | en | 0.75 | ✅ Live (Google News proxy) |
| JRP | Jerusalem Post | RSS | en | 0.70 | ✅ Live (Google News proxy) |
| CNN | CNN | RSS | en | 0.75 | ✅ Live (Google News proxy) |
| GUA | The Guardian | RSS | en | 0.78 | ✅ Live (theguardian.com) |
| MEE | Middle East Eye | RSS | en | 0.60 | ✅ Live (Google News proxy) |
| SDT | Sudan Tribune | RSS | en | 0.60 | ✅ Live |
| BBAR | BBC Arabic | RSS | ar | 0.80 | ✅ Live (feeds.bbci.co.uk/arabic) |
| SKA | Sky News Arabia | RSS | ar | 0.65 | ✅ Live (Google News proxy) |
| DW | Deutsche Welle Arabic | RSS | ar | 0.80 | ✅ Live |
| F24 | France 24 Arabic | RSS | ar | 0.80 | ✅ Live (Google News proxy) |
| ARB | Al Arabiya | RSS | ar | 0.65 | ✅ Live (Google News proxy) |
| ANA | Anadolu Agency | RSS | ar | 0.70 | ✅ Live |
| MND | Mondoweiss | RSS | en | 0.55 | ✅ Live |
| WAF | WAFA (Palestinian NA) | RSS | en | 0.65 | ✅ Live (Google News proxy) |
| AKH | Al-Akhbar Lebanon | RSS | ar | 0.55 | ✅ Live (Google News proxy) |
| EI | Electronic Intifada | RSS | en | 0.55 | ✅ Live |
| TAS | Tasnim (Iranian state) | RSS | ar | 0.40 | ✅ Live (Google News proxy) |
| PTV | Press TV | RSS | en | 0.40 | ✅ Live (Google News proxy) |
| RTA | RT Arabic | RSS | ar | 0.35 | ✅ Live (Google News proxy) |
| GG | Glenn Greenwald | RSS | en | 0.50 | ✅ Live (Substack) |
| GZ | The Grayzone | RSS | en | 0.40 | ✅ Live |
| CJ | Caitlin Johnstone | RSS | en | 0.35 | ✅ Live (Substack) |
| AW | Antiwar.com | RSS | en | 0.45 | ✅ Live (Google News proxy) |
| CRA | The Cradle | RSS | en | 0.45 | ✅ Live |
| DSN | Drop Site News | RSS | en | 0.55 | ✅ Live |
| BNO | BNO News | RSS | en | 0.50 | ✅ Live (bnonews.com/feed) |
| MAYE | Al Mayadeen EN | RSS | en | 0.45 | ✅ Live (almayadeen.net/rss) |
| AJA+ | AJ Plus Arabic | telegram_web | ar | 0.50 | ✅ Live (t.me/s/) |
| WM | War Monitor | telegram_web | en | 0.25 | ✅ Live (t.me/s/) |
| SI | Spectator Index | telegram_web | en | 0.10 | ✅ Live (t.me/s/) |
| YT_BP | Breaking Points | RSS | en | 0.35 | ✅ Live (YouTube RSS) |
| YT_DN | Democracy Now! | RSS | en | 0.50 | ✅ Live (YouTube RSS) |
| YT_RT | The Real News Network | RSS | en | 0.45 | ✅ Live (YouTube RSS) |
| AJE | Al Jazeera English | — | — | — | ⏸ Removed — same feed as AJA |
| BBC+ | BBC Breaking TG | — | — | — | ⏸ Removed — redundant with BBC RSS |
| ASH | Asharq Al-Awsat | — | ar | — | ⏸ Disabled — Render IPs blocked |
| TNA | The New Arab | — | en | — | ⏸ Disabled — Render IPs blocked |

## NLP Pipeline (task order per 15-min cycle)
| Task | File | What it does | Notes |
|------|------|--------------|-------|
| worker | ingestion_worker/worker.py | Fetch RSS/Telegram, dedup, write articles | In-memory URL hash set for dedup |
| task6 | task6_images.py | OG image backfill | Runs right after ingestion |
| task7 | task7_fetch_body.py | Fetch full article body via trafilatura | Prioritises articles in conflicts/pairs |
| task7_5 | task7_5_summarize.py | Groq LLM summaries | FAST_MODEL, per-article |
| task8/8b | task8_translate.py | Groq translation EN↔AR | Both directions |
| task9 | task9_embed.py | Sentence embeddings (MiniLM-L6-v2, 384-dim) | Model released after task9 to free RAM |
| task10 | task10_pairs.py | Cosine similarity pairing (threshold 0.70, 48h window) | Top-10 pairs per article, LIMIT 50 |
| task11 | task11_nli.py | Contradiction classification via **Groq** (FAST_MODEL) | See note below |
| task12 | task12_conflicts.py | Conflict scoring and storage | CONTRADICTION_THRESHOLD=0.55 |
| task13 | task13_bias_analysis.py | Framing analysis + emotion scoring via Groq | SMART_MODEL, BATCH_SIZE=5 |
| task14 | task14_translate_analysis.py | Translate framing analysis to Arabic | FAST_MODEL |
| task15 | task15_cleanup.py | Delete stale articles | Daily 02:00 UTC, ARTICLE_RETENTION_DAYS=90 |

### Task 11 — Important Note
**Previously used HF Inference API (mDeBERTa-v3, ~100 req/day free tier).** This caused the entire conflict pipeline to stall after 2 cycles/day — all pairs fell back to `neutral`, task12 got zero contradiction pairs, no conflicts were ever created.

**Fixed 2026-06-13:** task11 now uses Groq FAST_MODEL (14,400 req/day). `HF_TOKEN` is no longer required anywhere in the pipeline.

## DB Schema — Key Columns
### articles table
- `headline_en`, `headline_ar` — translated headlines
- `summary` — LLM-generated English summary (task7_5)
- `summary_ar` — Arabic summary (task8b)
- `embedding` — 384-dim vector (MiniLM-L6-v2, task9)
- `body_snippet` — first ~2000 chars of full body (task7)
- `processed_nlp` — bool, set true after task9 embeds the article

### conflicts table
- `framing_analysis` — JSONB, set by task13. Fields:
  - `dispute` — core question (≤12 words)
  - `narrative` — 2–3 sentence journalist analysis
  - `claims_a`, `claims_b` — one sentence per source
  - `key_question` — verification question for journalists
  - `factual_disagreement`, `framing_difference` — or null
  - `emotion_a`, `emotion_b` — per-source emotional register: `{anger, fear, sadness, hope, neutral}` each 0.0–1.0
  - `dispute_ar`, `narrative_ar`, `claims_a_ar`, `claims_b_ar`, `key_question_ar` — Arabic translations (task14)

### article_pairs table
- `status` — `pending` → `processed` | `error`
- `nli_label` — `contradiction` | `neutral` | `entailment`
- `contradiction_score` — float 0–1 (Groq confidence when label=contradiction)

## API Endpoints
- `GET /` — root health (HEAD supported)
- `GET /health` — db + article count + Groq daily usage + `db_size_mb` (passive Supabase storage tracking)
- `GET /api/v1/sources` — list active sources
- `GET /api/v1/feed?language=&source=&limit=&offset=` — paginated articles
- `GET /api/v1/conflicts?min_score=&limit=&offset=` — contradiction pairs (includes `source_1_profile`, `source_2_profile` — one-line editorial description injected server-side from SOURCE_PROFILE dict, no DB query)
- `GET /api/v1/conflicts/{id}` — single conflict detail (same profile fields)

## Frontend Features (crisis-lens-six.vercel.app)
- Single-file HTML/JS, auto-deploys to Vercel on push to main
- Arabic/English toggle with full RTL layout
- Comparison modal with:
  - **Conflict type badge** — FACTUAL DISPUTE (red) / FRAMING DIFFERENCE (blue) / MIXED (brown)
  - **Time delta** — "Published 3h apart" between the two articles
  - **Share button** — copies deep link `?conflict=<id>` to clipboard
  - **Framing analysis panel** — dispute hook, narrative, claims per source, key question, factual/framing breakdowns
  - **Emotion chart** — horizontal bars for anger/fear/sadness/hope/neutral per source, colour-coded by region
  - **Source editorial profiles** — one-line description under each source name
  - **Read original buttons** — source-coloured links to the actual articles (↗)
  - **Signal breakdown** — contradiction %, similarity %, weighted score, trust scores (collapsible)
- Methodology panel with Source Ethics section explaining state media inclusion
- ToS and Privacy Policy modals (footer links)
- Cold-start overlay with spinner and retry button

## Known Issues & Workarounds
1. **Redis removed:** Replaced with in-memory URL hash set. No external dependency needed.
2. **Telegram MTProto blocked:** Render Frankfurt IPs cannot complete MTProto TLS handshake. **Fixed:** Switched to `TelegramWebAdapter` which scrapes `t.me/s/<channel>` (plain HTTPS public preview — no auth, no MTProto). All 6 channels now live.
3. **ASH/TNA RSS blocked:** aawsat.com and newarab.com block Render Frankfurt IPs. Fix: use alternative feeds or proxy.
4. **API /health hangs:** DB connections in health check can hang. Fixed with `socket_timeout=3` and `statement_timeout=3000ms`.
5. **feed_type_check constraint:** Original DB constraint didn't include `telegram_web`. Startup migration in `main.py` dynamically detects and drops the old constraint before inserting Telegram sources.
6. **IPv6 unreachable:** Supabase direct connection is IPv6-only. Render/Railway both IPv4. Always use Session Pooler URL — see Infrastructure section.
7. **HF Inference API rate limit (RESOLVED):** Was ~100 req/day; caused task11 to silently return `neutral` for all pairs after 2 cycles, blocking all conflict creation. Fixed by migrating task11 to Groq (2026-06-13).
8. **task9 model 404 on Railway:** `paraphrase-multilingual-MiniLM-L6-v2` does NOT exist on HuggingFace (cached on first run; 404 on fresh Railway container). Reverted to `paraphrase-multilingual-MiniLM-L12-v2` — the only multilingual MiniLM that exists. Railway Hobby (~1GB RAM) handles L12 fine. Explicit model release after task9 still in place. Also requires `HF_TOKEN` env var on Railway (HuggingFace requires auth even for public model downloads).

## Legal
- `LEGAL.md` in repo root — EU Copyright Directive Article 15 analysis, publisher contact for takedowns, data retention policy.
- Privacy Policy and ToS accessible via footer links on the site.
- Policy: index headlines and snippets only (no full article reproduction). Publisher takedown contact: 7daniben@gmail.com.

## Phase Status
- ✅ Phase 1 — Pipeline MVP (complete)
- ✅ Phase 2 — NLP MVP (complete: translation, multilingual embeddings, Groq-powered NLI, framing analysis with emotion scoring, Arabic translation of analysis, stale cleanup)
- ✅ Phase 3 — Website MVP (complete: single-file HTML/JS deployed to Vercel — Next.js not needed)
  - Frontend: https://crisis-lens-six.vercel.app
  - Auto-deploys on push to main
- 🔜 Phase 4 — Expansion (more sources, framing classifier, custom domain)

## Pending
- Al Jazeera DACR credentials: Reference VPHX98C923 — switch AJA to Arabic RSS when received
- Custom domain — set ALLOWED_ORIGINS in Render env when domain is finalised
- Railway trial expires ~2026-07-04 — upgrade to Hobby ($5/mo) before then
- Rotate Supabase DB password — it appeared in Railway logs in plaintext during DATABASE_URL debugging

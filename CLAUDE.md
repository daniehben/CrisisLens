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
- **Render Web Services** (both free tier, Frankfurt):
  - crisislens-api: https://crisislens-api.onrender.com — LIVE ✅
  - crisislens-worker: https://crisislens-worker.onrender.com — LIVE ✅

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

## Known Issues & Workarounds
1. **Redis removed:** Replaced with in-memory URL hash set. No external dependency needed.
2. **Telegram MTProto blocked:** Render Frankfurt IPs cannot complete MTProto TLS handshake. **Fixed:** Switched to `TelegramWebAdapter` which scrapes `t.me/s/<channel>` (plain HTTPS public preview — no auth, no MTProto). All 6 channels now live.
3. **ASH/TNA RSS blocked:** aawsat.com and newarab.com block Render Frankfurt IPs. Fix: use alternative feeds or proxy.
4. **API /health hangs:** DB and Redis connections in health check can hang. Fixed with `socket_timeout=3` and `statement_timeout=3000ms`.

## API Endpoints
- `GET /` — root health (HEAD supported)
- `GET /health` — db + redis + article count
- `GET /api/v1/sources` — list active sources
- `GET /api/v1/feed?language=&source=&limit=&offset=` — paginated articles
- `GET /api/v1/conflicts?min_score=&limit=&offset=` — contradiction pairs
- `GET /api/v1/conflicts/{id}` — single conflict detail

## Phase Status
- ✅ Phase 1 — Pipeline MVP (complete, 78+ articles live)
- 🔜 Phase 2 — NLP MVP (language detection, translation, AraBERT embeddings, contradiction detection)
- 🔜 Phase 3 — Website MVP (Next.js + Vercel)
- 🔜 Phase 4 — Expansion

## Pending
- Al Jazeera DACR credentials: Reference VPHX98C923 — switch AJA to Arabic RSS when received
- Redis connectivity fix needed before Phase 2
- Telegram adapter fix needed for Phase 4 source expansion
# CrisisLens — CLAUDE.md

## Project Overview
Real-time Arabic-first conflict news aggregation platform. Zero budget, free-tier infrastructure.

## Infrastructure
- **GitHub:** github.com/daniehben/CrisisLens (main branch)
- **Render PostgreSQL:** crisislens-db, Frankfurt free tier
  - Internal URL: `postgresql://crisislens_db_user:KKdadOnM5ftKPBdsJhcpewcw0EoppVYW@dpg-d6ms1sn5r7bs73cl59tg-a/crisislens_db`
  - External URL: append `?sslmode=require` and use `.frankfurt-postgres.render.com` host
- **Render Redis (Valkey):** crisislens-redis, Frankfurt free tier
  - Internal URL: `redis://red-d6ms42sr85hc73dav9l0:6379` — NO TLS (internal network only, no password)
  - ⚠️ Redis is currently NOT reachable from worker — connection refused on private network. Deduplication bypassed.
- **Render Web Services** (both free tier, Frankfurt):
  - crisislens-api: https://crisislens-api.onrender.com — LIVE ✅
  - crisislens-worker: https://crisislens-worker.onrender.com — LIVE ✅

## Render Deployment Notes (CRITICAL)
- Both services must be `type: web` in render.yaml — `type: worker` has no HTTP server and times out
- Health check path must be EMPTY in Render settings — do NOT set it to `/health`
- Root endpoint must accept HEAD requests: `@app.api_route("/", methods=["GET", "HEAD"])`
- Add `PYTHONUNBUFFERED=1` env var to worker for visible logs
- Free tier spins down after inactivity — first request takes 50+ seconds

## Active Sources (Phase 1)
| Code | Name | Type | Language | Trust | Status |
|------|------|------|----------|-------|--------|
| AJA | Al Jazeera | RSS | en | 1.00 | ✅ Live |
| AJE | Al Jazeera English | NewsAPI | en | 0.80 | ✅ Live |
| BBC | BBC News | NewsAPI | en | 0.80 | ✅ Live |
| JRP | Jerusalem Post | NewsAPI | en | 0.75 | ✅ Live |
| WP | Washington Post | NewsAPI | en | 0.80 | ✅ Live |
| AP | Associated Press | NewsAPI | en | 0.80 | ✅ Live |
| ASH | Asharq Al-Awsat | RSS | ar | 0.65 | ⏸ Disabled — Render IPs blocked |
| TNA | The New Arab | RSS | en | 0.65 | ⏸ Disabled — Render IPs blocked |
| BNO | BNO News | Telegram | en | 0.50 | ⏸ Disabled — MTProto blocked |
| AJA+ | AJ Plus Arabic | Telegram | ar | 0.50 | ⏸ Disabled — MTProto blocked |
| AJE+ | Al Jazeera English TG | Telegram | en | 0.80 | ⏸ Disabled — MTProto blocked |
| REU | Reuters Telegram | Telegram | en | 0.80 | ⏸ Disabled — MTProto blocked |
| BBC+ | BBC Breaking TG | Telegram | en | 0.80 | ⏸ Disabled — MTProto blocked |
| WM | War Monitor | Telegram | en | 0.25 | ⏸ Disabled — MTProto blocked |
| SI | Spectator Index | Telegram | en | 0.10 | ⏸ Disabled — MTProto blocked |

## Known Issues & Workarounds
1. **Redis unreachable:** `Connection refused` on internal network. Worker uses DB-level `ON CONFLICT DO NOTHING` for deduplication instead.
2. **Telegram blocked:** Render Frankfurt IPs cannot complete MTProto TLS handshake. Fix: migrate to async Telethon with explicit proxy or use Telegram Bot API instead.
3. **ASH/TNA RSS blocked:** aawsat.com and newarab.com block Render Frankfurt IPs. Fix: use alternative feeds or proxy.
4. **API /health hangs:** DB and Redis connections in health check can hang. Fixed with `socket_timeout=3` and `statement_timeout=3000ms`.

## API Endpoints
- `GET /` — root health (HEAD supported)
- `GET /health` — db + redis + article count
- `GET /api/v1/sources` — list active sources
- `GET /api/v1/feed?language=&source=&limit=&offset=` — paginated articles

## Phase Status
- ✅ Phase 1 — Pipeline MVP (complete, 78+ articles live)
- 🔜 Phase 2 — NLP MVP (language detection, translation, AraBERT embeddings, contradiction detection)
- 🔜 Phase 3 — Website MVP (Next.js + Vercel)
- 🔜 Phase 4 — Expansion

## Pending
- Al Jazeera DACR credentials: Reference VPHX98C923 — switch AJA to Arabic RSS when received
- Redis connectivity fix needed before Phase 2
- Telegram adapter fix needed for Phase 4 source expansion
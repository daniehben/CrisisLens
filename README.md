# CrisisLens 🔍

Real-time conflict news aggregation platform with AI-powered contradiction detection.

CrisisLens ingests articles from 8+ sources across Arabic and English media, translates and embeds them using multilingual NLP models, and detects when different sources report contradictory facts about the same event — weighted by source trust.

## Architecture
```
Sources → Ingestion Worker → PostgreSQL
                                ↓
              NLP Pipeline (translation → embeddings → similarity → NLI → conflicts)
                                ↓
                          REST API → (Phase 3: Next.js Dashboard)
```

## Stack

- **Backend:** Python, FastAPI, APScheduler
- **Database:** PostgreSQL + pgvector (Render)
- **Cache:** Redis / Upstash
- **NLP:** deep-translator, sentence-transformers (MiniLM), NLI classifier
- **Infrastructure:** Render (free tier), RSSHub (self-hosted)

## Sources

| Code | Name | Type | Language | Trust |
|------|------|------|----------|-------|
| AJA | Al Jazeera | RSS | en | 1.00 |
| AJE | Al Jazeera English | NewsAPI | en | 0.80 |
| BBC | BBC News | NewsAPI | en | 0.80 |
| WP | Washington Post | NewsAPI | en | 0.80 |
| AP | Associated Press | NewsAPI | en | 0.80 |
| JRP | Jerusalem Post | NewsAPI | en | 0.75 |
| AJA+ | AJ Plus Arabic | RSSHub/Telegram | ar | 0.50 |

## API

Base URL: `https://crisislens-api.onrender.com`

| Endpoint | Description |
|----------|-------------|
| `GET /` | Health check |
| `GET /health` | DB + Redis status |
| `GET /api/v1/sources` | List active sources |
| `GET /api/v1/feed` | Paginated articles (`?language=ar&limit=20&offset=0`) |
| `GET /api/v1/conflicts` | Detected contradictions (Phase 2) |

## Project Status

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Ingestion pipeline | ✅ Complete |
| Phase 1.5 | Prerequisites (Redis, pgvector, Arabic source) | ✅ Complete |
| Phase 2 | NLP MVP (translation, embeddings, contradiction detection) | 🔄 In Progress |
| Phase 3 | Next.js dashboard | 🔜 Planned |
| Phase 4 | Source expansion, optimization | 🔜 Planned |

## Local Development
```bash
# Start local DB and Redis
docker-compose up -d

# Run migrations
python migrations/run_migrations.py

# Start worker
python -m backend.ingestion_worker.scheduler

# Start API
uvicorn backend.api_server.main:app --reload --port 8000
```

## Environment Variables

| Variable | Service | Description |
|----------|---------|-------------|
| `DATABASE_URL` | both | PostgreSQL connection string |
| `REDIS_URL` | both | Upstash Redis URL (rediss://) |
| `NEWSAPI_KEY` | worker | NewsAPI.org API key |
| `TELEGRAM_API_ID` | worker | Telegram MTProto API ID |
| `TELEGRAM_API_HASH` | worker | Telegram MTProto API hash |
| `TELEGRAM_SESSION_B64` | worker | Base64-encoded Telegram session |

---

Built by [@daniehben](https://github.com/daniehben)

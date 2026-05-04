# CrisisLens — System Architecture

A snapshot of how the system is wired together as of May 2026. Use this as the map when you need to make a change and aren't sure where it lives.

---

## High-level flow

```
   ┌─────────────────┐    ┌─────────────────┐    ┌────────────────────┐
   │ External news   │───▶│  Ingestion      │───▶│  Postgres + pgvector│
   │ (RSS, NewsAPI)  │    │  worker (15min) │    │  (Supabase)         │
   └─────────────────┘    └────────┬────────┘    └──────────┬──────────┘
                                   │                        │
                                   ▼                        │
                          ┌────────────────┐                │
                          │  NLP pipeline  │                │
                          │  (5 sequential │                │
                          │  tasks 8–12)   │────────────────┘
                          └────────────────┘
                                                            ▲
   ┌─────────────────┐    ┌─────────────────┐               │
   │ Browser         │───▶│  FastAPI        │───────────────┘
   │ (frontend SPA)  │    │  (read-only)    │
   └─────────────────┘    └─────────────────┘
```

Two services, one database. The worker writes; the API reads. They communicate exclusively through Postgres — no message queue, no shared memory.

---

## Repository layout

```
~/Desktop/CrisisLens/
├── backend/
│   ├── api_server/              FastAPI read service
│   │   ├── main.py              endpoints: /health, /api/v1/sources, /api/v1/feed, /api/v1/conflicts
│   │   └── schemas.py           pydantic response models
│   ├── ingestion_worker/        APScheduler-driven write service
│   │   ├── scheduler.py         entry point — boots health server, runs initial cycle, schedules next
│   │   ├── worker.py            ingestion cycle: parallel fetch + serial DB write
│   │   ├── db_writer.py         write_batch with ON CONFLICT dedup
│   │   └── adapters/
│   │       ├── base.py          FeedAdapter ABC
│   │       ├── rss_adapter.py   RSS sources (AJA, AJA+, DW, F24, ARB)
│   │       ├── newsapi_adapter.py  NewsAPI sources (AJE, BBC, JRP, WP, AP)
│   │       └── telegram_adapter.py Telethon-based, currently disabled
│   ├── nlp_pipeline/            sequential task chain
│   │   ├── task8_translate.py   detect language + translate EN→AR via deep-translator
│   │   ├── task9_embed.py       embed Arabic headlines via HF MiniLM (384-dim)
│   │   ├── task10_pairs.py      cosine similarity pair finding (threshold 0.70)
│   │   ├── task11_nli.py        NLI classification via HF mDeBERTa multilingual
│   │   └── task12_conflicts.py  weighted conflict scoring + insertion
│   └── shared/
│       ├── config.py            Config dataclass loading from .env
│       ├── database.py          psycopg2 connection helper + get_source_map cache
│       ├── deduplication.py     Redis bitmap dedup with safe no-op fallback
│       └── models.py            RawArticle dataclass (boundary type from adapters)
├── frontend/
│   └── index.html               single-file vanilla JS conflict viewer
├── migrations/
│   ├── 001_create_tables.sql    sources / events / articles / conflicts / ingestion_logs
│   ├── 002_create_indexes.sql   HNSW + B-tree indexes
│   ├── 003_seed_sources.sql     initial source rows
│   ├── 004_fix_schema_and_new_sources.sql  trust_weight on articles, more sources
│   ├── 005_nlp_schema.sql       processed_nlp, headline_ar_translated, article_pairs
│   ├── 006_fix_embedding_dimension.sql  vector(384) instead of vector(768)
│   ├── 007_more_arabic_sources.sql      DW, France 24, Al Arabiya
│   └── run_migrations.py        auto-discovers NNN_*.sql files
├── scripts/
│   ├── label_conflicts.py       export top-N conflicts to CSV for manual labeling
│   └── score_labels.py          read labeled CSV → precision per threshold bucket
├── docs/
│   ├── ARCHITECTURE.md          this file
│   ├── TROUBLESHOOTING.md       errors hit + fixes
│   └── CHANGELOG.md             chronological session log
├── tests/
│   └── test_deduplication.py    one test, the rest of the codebase has no coverage yet
├── requirements.txt
├── render.yaml                  declares both Render services + env var keys
├── docker-compose.yml           local Postgres + Redis for development
├── Procfile                     fallback for non-Render deploy
└── CLAUDE.md                    project context for AI assistants
```

---

## Data model

Five tables, all in Postgres `public` schema.

**`sources`** — the catalog of news outlets we ingest from. One row per outlet. Trust tier (1–5) and trust weight (0.00–1.00) are static metadata used for scoring. `is_active` toggles ingestion without dropping the row.

**`articles`** — the canonical article record, one row per ingested item. Key fields:
- `(source_id, external_id)` UNIQUE — primary dedup key, enforced by DB
- `url` UNIQUE — secondary dedup
- `headline_en`, `headline_ar` — at least one must be non-null
- `embedding vector(384)` — populated by task9, indexed with HNSW for cosine search
- `processed_nlp` BOOLEAN — set TRUE after embedding stored
- `headline_ar_translated` BOOLEAN — TRUE if `headline_ar` was machine-translated from English

**`article_pairs`** — candidate "these two articles might be about the same event" pairs from cosine similarity. Created in task10. Lifecycle: `status='pending'` → task11 classifies → `status='processed'` with `nli_label` set. Canonical ordering enforced (`article_id_1 < article_id_2`) so pair (A,B) and (B,A) can't both exist.

**`conflicts`** — pairs the NLI model flagged as contradiction AND that passed the weighted scoring threshold. The user-facing "we found a contradiction" record. Has `weighted_score` for ranking and `is_resolved` for moderation later.

**`ingestion_logs`** — append-only audit of what each ingestion cycle did per source. Useful for debugging "why are we not getting X anymore."

**`events`** — defined in the schema but currently unused. Designed for future "these 12 articles all cover the same incident" clustering.

---

## NLP pipeline — what each task actually does

The pipeline is intentionally a **sequential SQL state machine**. Each task pulls "rows that haven't been processed yet" using a flag column or join, processes a small batch, updates the row, and stops. This means:
- Restarting mid-cycle is safe — work resumes from wherever it left off
- Tasks can be tested independently
- No coordination between tasks beyond reading what the previous one wrote

**Task 8 — Translate.** Pulls articles where `processed_nlp = FALSE` and `headline_ar IS NULL`. For each, strips HTML from the body snippet and translates the English headline to Arabic via Google Translate (free, no API key, scrapes the public web translator). Writes back to `headline_ar` and sets `headline_ar_translated = TRUE`. Articles already in Arabic skip translation. Batched with `translate_batch` for ~16x speedup.

**Task 9 — Embed.** Pulls articles where `headline_ar IS NOT NULL` and `embedding IS NULL`. Sends headlines to HuggingFace's hosted `paraphrase-multilingual-MiniLM-L12-v2` (free Inference API, 384-dim sentence embeddings). Stores the vector and flips `processed_nlp = TRUE`. Batched 16 at a time with per-text fallback.

**Task 10 — Pair generation.** For each newly embedded article, runs a cosine-similarity search against all other embedded articles in the past 48 hours **from a different source**, with similarity ≥ 0.70. Inserts up to top-10 candidate pairs into `article_pairs` with `status='pending'`. Uses canonical ordering (`min(a,b), max(a,b)`) to avoid (A,B) and (B,A) duplicates.

**Task 11 — NLI classification.** For each `pending` pair, calls HF `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` with the input formatted as `premise</s></s>hypothesis`. Prefers Arabic headlines when both pairs have them (the model is multilingual; preserving the original language preserves signal). Stores the winning label (`contradiction` / `neutral` / `entailment`) and the contradiction probability. Sets `status='processed'`.

**Task 12 — Conflict scoring.** Pulls processed pairs where `nli_label = 'contradiction'` and `contradiction_score ≥ 0.55` (raised from 0.30 after tuning). Computes `conflict_score = contradiction_score × max(trust_a, trust_b)`. If above the 0.30 threshold, inserts into `conflicts`. Skips pairs already converted (using `NOT EXISTS` against `(article_a_id, article_b_id)` in both orderings).

---

## Frontend

Single static HTML file at `frontend/index.html`. No build step, no Node, no React. Tailwind via CDN, IBM Plex Sans Arabic font from Google Fonts, vanilla `fetch()` to the API.

Key UX decisions:
- **Bilingual with toggle** — language preference persists in `localStorage` so the visitor's choice sticks across reloads.
- **RTL-aware** — Arabic headlines get `direction: rtl` and the Arabic-specific font; English gets the default.
- **Trust bars** — every source shows a "High / Medium / Low / Unverified" trust label and a colored bar. Makes the trust weighting visible to non-technical users without explaining the algorithm.
- **Skeleton loading** — animated placeholders during the API fetch (free Render API can take 50+s on cold start; otherwise the page looks broken).

Deployed as a Render Static Site at `crisislens-5cx9.onrender.com`. Auto-redeploys on push to `main`. No backend, just static hosting.

---

## API endpoints

`crisislens-api.onrender.com`:

- `GET /` — health (returns `{"status":"ok"}`, supports HEAD for uptime monitors)
- `GET /health` — DB count + Redis status, with 3s statement timeout so it can't hang
- `GET /api/v1/sources` — list active sources
- `GET /api/v1/feed?language=&source=&limit=&offset=` — paginated articles
- `GET /api/v1/conflicts?min_score=&limit=&offset=` — sorted by `weighted_score DESC`

All endpoints rate-limited to 60/min/IP via `slowapi`. CORS is `*` for now; tighten before launch.

---

## Infrastructure

| Component | Service | Tier | Notes |
|-----------|---------|------|-------|
| API server | Render web | Free | Frankfurt, spins down at 15 min idle |
| Worker | Render web | Free | Frankfurt, ditto |
| Postgres + pgvector | Supabase | Free 500MB | Migrated from Render after expiration |
| Redis | (none active) | — | Was Upstash, now broken; safe no-op fallback in code |
| RSSHub bridge | Render | Free | `crisislens-rsshub.onrender.com`, used for AJ+ Arabic |
| Frontend | Render Static | Free | `crisislens-5cx9.onrender.com` |
| GitHub | — | Free | `daniehben/CrisisLens`, public |
| HF Inference | HuggingFace | Free tier | Both embedding + NLI calls |
| NewsAPI | newsapi.org | Free 100/day | Hard quota, hit by 5 sources × 96 cycles theoretically |
| Translation | deep-translator | Free | Scrapes public Google Translate, no API key |

Total monthly cost: **$0**.

---

## Active sources (as of latest deploy)

| Code | Name | Type | Lang | Trust | Status |
|------|------|------|------|-------|--------|
| AJA | Al Jazeera | RSS | en* | 1.00 | Live |
| AJA+ | AJ Plus Arabic | RSSHub | ar | 0.50 | Live |
| DW | Deutsche Welle Arabic | RSS | ar | 0.80 | New — being verified |
| F24 | France 24 Arabic | RSS | ar | 0.80 | New — being verified |
| ARB | Al Arabiya | RSS | ar | 0.65 | New — being verified |
| AJE | Al Jazeera English | NewsAPI | en | 0.80 | Live |
| BBC | BBC News | NewsAPI | en | 0.80 | Live |
| JRP | The Jerusalem Post | NewsAPI | en | 0.75 | Live |
| WP | The Washington Post | NewsAPI | en | 0.80 | Live |
| AP | Associated Press | NewsAPI | en | 0.80 | Live |

\* AJA pulls the English Al Jazeera RSS feed pending DACR (Arabic) credentials. The flag `language: 'en'` in `RSS_SOURCES` reflects this.

---

## Conventions worth following

- **Migrations are idempotent.** Every `CREATE TABLE` uses `IF NOT EXISTS`. Every `ALTER TABLE ADD COLUMN` uses `IF NOT EXISTS`. Re-running the suite against a healthy DB is a no-op.
- **All times are UTC.** `TIMESTAMPTZ` on the schema side, `datetime.now(timezone.utc)` on the Python side.
- **Adapters never raise.** They return an empty list and log on any failure, so a single broken source can't crash the worker.
- **API never blocks indefinitely.** Every endpoint has either an explicit timeout or rate limit; DB calls use `SET statement_timeout`.
- **NLP tasks are restartable.** Each one filters by a "not yet processed" flag; killing the worker mid-cycle is safe.

---

## What's deliberately not built yet

These are open opportunities, not gaps:

- **Telegram via Bot API** — currently the Telethon adapter is wired but disabled (Render Frankfurt blocks MTProto). Switching to Bot API would unlock 5 high-trust sources (Reuters, BBC Breaking, AJE TG, BNO, AJ+).
- **Same-story collapse** — pairs from the same event get scored as contradictions even when they're just different framings (false positive #3 in the labeled set). Needs entity overlap or near-duplicate detection.
- **Event clustering** — `events` table exists but isn't populated. Future: cluster articles into events so the UI can show "5 sources reporting on the Lebanon strikes" rather than just pairwise contradictions.
- **Auth + moderation** — anyone can hit the API. Once there's traffic, add a key.
- **Tests** — only `test_deduplication.py` exists. NLP threshold changes are currently validated by spot-checking the labeling CSV.
- **Observability** — `ingestion_logs` is being written but no dashboard reads it. A `/api/v1/admin/health` page showing per-source success rates would catch silent failures.

# CrisisLens — Session Log

A chronological record of every change made during the May 2026 build session. Each entry: what changed, why, and the commit.

---

## Session start state (May 3, 2026)

- Backend pipeline (Phases 1 + 2) live on Render free tier, serving from `crisislens-api.onrender.com`
- ~78 articles ingested
- Frontend folder empty
- Several silent bugs in the NLP pipeline producing zero useful output
- DB on Render free Postgres (about to expire)

---

## Round 1 — Code quality and performance fixes

**Commit `35a024a` — Pipeline fixes: parallel fetches, batched HF calls, Redis no-op, task12 ID bug**

Five interlocking fixes in a single commit:

1. **`task12_conflicts.py` ID-space bug** — the "skip already-processed pairs" filter compared `pair_id` (article_pairs PK) against `article_id` (articles PK). Two unrelated integer columns. Replaced with `NOT EXISTS` keyed on the actual `(article_a_id, article_b_id)` tuple in both orderings. See Troubleshooting #1.

2. **Redis dedup graceful degradation** — `get_redis_client()` now probes the connection once with a 2s timeout and returns `None` on failure. `check_and_mark()` accepts `None` and no-ops. A module-level `_redis_disabled` flag prevents repeated probe attempts. Cycle time on a Redis-down system dropped from ~minutes of dead waiting to milliseconds. See Troubleshooting #2.

3. **HF embeddings batched** — `task9_embed.py` now sends 16 texts per request instead of 1. Per-text fallback if a batch fails. Effective speedup on 100 articles: ~16x.

4. **Google translate batched** — `task8_translate.py` uses `translate_batch` with the same fallback pattern.

5. **Source fetches parallelized** — `worker.py` runs all adapter `.fetch()` calls in a `ThreadPoolExecutor(max_workers=6)`. Cycle wall time drops from sum-of-fetches to max-of-fetches (3-5x in practice). Per-adapter wrapper (`_fetch_one`) ensures a single source's failure doesn't crash the pool.

**Commit (untagged) — Cache source_map per cycle, fail-fast on missing config**

- `worker.py` now fetches `source_map` once per cycle and threads it into `write_batch(source_map=...)`. Avoids re-querying the sources table on every batch.
- `db_writer.write_batch` made the source_map argument optional for backward compat.
- `scheduler.py` now calls `Config.validate()` before the first cycle so missing env vars fail loudly at boot rather than silently mid-cycle.
- `config.py` relaxed: only `DATABASE_URL` and `NEWSAPI_KEY` are required. Telegram credentials downgraded to a soft warning since Telegram sources are currently disabled.

---

## Round 2 — Database migration

**Why:** Render's free Postgres tier expired (~90 days from creation). All connections returned `SSL connection has been closed unexpectedly`. See Troubleshooting #7.

**What:** Migrated to Supabase free tier (500MB, no expiration), `eu-central-1` region.

Steps taken:
1. Created Supabase project "CrisisLens"
2. Got the **Session pooler** connection string (port 5432, IPv4-compatible — needed because the direct connection is IPv6-only on Supabase free tier and most networks can't reach it)
3. Enabled `pgvector` extension via Supabase Dashboard → Database → Extensions
4. Tested connection with `psql` → returned `1` from `SELECT 1`
5. Updated `DATABASE_URL` env var on both Render services (api + worker)

**Commit (in scripts) — `migrations/005_nlp_schema.sql`**

After running 001–004 against Supabase, discovered the NLP code referenced columns and a table that didn't exist in source control:
- `articles.processed_nlp BOOLEAN`
- `articles.headline_ar_translated BOOLEAN`
- `article_pairs` table (with status, nli_label, contradiction_score columns)

These had been manually added to the old Render DB and never committed. Migration 005 adds them idempotently with proper indexes (partial index on `processed_nlp = FALSE` for the "find unprocessed" hot path).

Also rewrote `run_migrations.py` to auto-discover all `NNN_*.sql` files instead of hardcoding the list. Future migrations just need to be dropped in the folder.

---

## Round 3 — NLP pipeline correctness

**Commit — `migrations/006_fix_embedding_dimension.sql` + `001_create_tables.sql` update**

- `paraphrase-multilingual-MiniLM-L12-v2` produces 384-dim vectors. Schema declared `vector(768)`. First HF API call succeeded, then Postgres rejected the insert with `expected 768 dimensions, not 384`.
- Migration 006 drops the column + HNSW index and recreates them at the right dimension. Updated 001 for fresh setups.

**Commit `511fd76` — Switch NLI to multilingual mDeBERTa, prefer Arabic input**

- Replaced `task11_nli.py` entirely. Old code was using `bart-large-mnli` via the **zero-shot-classification** pipeline, which asks "is this text *about* the topic 'contradiction'?" — completely different question from "do these two sentences contradict each other?". All 4 pairs were being labeled `neutral` with score `0.0000`.
- Switched to `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` — a true multilingual NLI model.
- Prefers Arabic headlines when both pairs have them (preserves signal vs. translating to English first).
- See Troubleshooting #10.

**Commit — Force text-classification pipeline + return all NLI scores**

- HF Inference router was auto-routing the new mDeBERTa model to its zero-shot pipeline by default. Returned HTTP 400 with `zero-shot-classification expects inputs to be either a string or a dict containing the key text or sequence`.
- Forced the right pipeline by appending `/pipeline/text-classification` to the URL.
- Added `"parameters": {"top_k": null}` to get scores for all 3 NLI classes (entailment / neutral / contradiction) instead of just the winning one.
- See Troubleshooting #11.

After this, NLI started producing real scores. Pairs labeled:
- Pair 1 (Trump Germany troops): contradiction 0.6585
- Pair 2 (Trump Iran statements): contradiction 0.9986
- Pair 4 (Israel airstrike casualties): contradiction 0.8228

---

## Round 4 — Conflict scoring fix

**Commit — Fix task12 scoring: drop trust_diff multiplier, weight by max_trust**

- Strong contradictions were not becoming conflicts. Cause: `conflict_score = contradiction_score × trust_diff × max_trust`. When two equal-trust sources contradicted (Al Jazeera 1.0 vs AP 0.8 → diff=0.2), the score was crushed to ~0.06 — below the 0.10 threshold.
- Replaced with `conflict_score = contradiction_score × max_trust`. Rewards rather than gates equal-trust contradictions.
- Bumped `CONFLICT_SCORE_THRESHOLD` from 0.10 → 0.15 to compensate.
- See Troubleshooting #12.

After this, 3 conflicts appeared: Trump Iran (0.998), NATO troops (0.925, false positive), Israel airstrikes (0.823).

---

## Round 5 — Phase 3: Frontend

**Commit — Phase 3: minimal conflict viewer frontend**

Built `frontend/index.html` as a single self-contained file:
- No Next.js, no build step, no Node — just HTML + Tailwind CDN + vanilla JS
- Bilingual with persisted language toggle (localStorage)
- RTL layout when Arabic-primary
- IBM Plex Sans Arabic for Arabic text, Inter for English
- Trust bars and contradiction confidence shown on each card
- Skeleton loading state for the slow Render free-tier cold start

Deployed as a Render Static Site → `crisislens-5cx9.onrender.com`. Auto-redeploys on push to `main`. Branch=`main`, publish dir=`frontend`, no build command needed.

---

## Round 6 — Threshold tuning

**Commit — Tune thresholds: contradiction 0.55, similarity 0.70**

After labeling the first 5 conflicts (2 real, 3 false positive — 40% precision):
- Bumped `CONTRADICTION_THRESHOLD` from 0.30 → 0.55 (kills the 0.4969 borderline false positive)
- Bumped `CONFLICT_SCORE_THRESHOLD` from 0.15 → 0.30
- Lowered `SIMILARITY_THRESHOLD` from 0.75 → 0.70 (more candidate pairs)

Acknowledged that thresholds alone can't fix the deeper "same story, different angle" failure mode. Real fixes need either entity-overlap heuristics or more source diversity.

---

## Round 7 — Source expansion (current)

**Commit — Source expansion: DW, France 24, Al Arabiya (Arabic RSS)**

Added 3 new Arabic-language RSS sources accessible from Render Frankfurt:
- **DW** (Deutsche Welle Arabic) — German state broadcaster, EU-hosted, trust 0.80
- **F24** (France 24 Arabic) — French international broadcaster, trust 0.80
- **ARB** (Al Arabiya) — was seeded but never wired up; fixed in migration 007 (changed feed_type from `mrss` to `rss`)

Updated:
- `rss_adapter.py` `RSS_SOURCES` dict
- `worker.py` `get_all_adapters` to instantiate the new RSS adapters
- New migration `007_more_arabic_sources.sql` to seed/update DB rows

Goal: more sources covering Israel/Lebanon/Iran in Arabic increases the chance of finding real cross-source contradictions (especially Arabic-vs-English on contested events).

---

## What's queued next

- Wait ~24 hours for the worker to accumulate 30+ pairs from the new sources
- Re-export and re-label conflicts to get a meaningful precision number
- Decide on next major work: same-story collapse heuristic, Telegram Bot API, or RSSHub bridges for Asharq + The New Arab

---

## Notable decisions worth re-reading

- **Stayed with single-file HTML over Next.js** — speed-to-deploy beat framework polish for a demo whose value is the data, not the UX.
- **Migrated to Supabase over re-creating Render Postgres** — same effort, no expiration headache, supports pgvector out of the box.
- **Kept worker as `type: web`** instead of `type: worker` — Render needs the bound HTTP port to consider the deploy healthy.
- **Chose mDeBERTa over English-only NLI models** — preserves Arabic signal that translation would lose, even though English-only models like `roberta-large-mnli` are better-known.

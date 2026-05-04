# Troubleshooting Log

A reference of every bug, infra failure, and config issue hit while building CrisisLens — what it looked like, what caused it, and how it was fixed. Use this when you hit something new and want to see if it rhymes with something you've already solved.

---

## 1. NLP pipeline: silent ID-space comparison in `task12_conflicts.py`

**Symptom:** Conflicts table stayed sparse even when many `article_pairs` had been classified as contradictions. Some pairs that should have been processed were silently dropped from re-processing.

**Diagnosis:** The "skip already-processed pairs" filter was wrong:

```sql
WHERE ap.pair_id NOT IN (
    SELECT article_a_id FROM conflicts
    UNION
    SELECT article_b_id FROM conflicts
)
```

`ap.pair_id` is the primary key of `article_pairs` (e.g. pair #47). `conflicts.article_a_id` is a foreign key to `articles.article_id` (e.g. article #47). Two different ID spaces, both happen to be integers — SQL couldn't catch the mistake. The query was effectively skipping random pairs based on a meaningless number collision.

**Fix:** Use `NOT EXISTS` against the actual `(article_a_id, article_b_id)` tuple in both orderings.

**Lesson:** Whenever you have multiple integer IDs in the same query, be explicit about which space you're comparing against. SQL won't help you — types are too loose.

---

## 2. Redis `socket_timeout` cost on every article when Redis is unreachable

**Symptom:** Worker cycles took several minutes longer than expected. Logs showed nothing wrong — just slow.

**Diagnosis:** The Render internal Redis service was unreachable, but `get_redis_client()` returned a client object without testing the connection. Every `check_and_mark(r, url)` call then blocked for 10 seconds (`socket_timeout=10`) before failing. With ~50 articles per cycle, that's ~8 minutes of dead waiting.

**Fix:** Probe Redis once at the start of each run with a short timeout (2s). If it fails, set a module-level `_redis_disabled` flag and have all subsequent calls return immediately. The DB-level `ON CONFLICT (source_id, external_id) DO NOTHING` in `db_writer.write_article` is the real dedup safety net — Redis is just an optimization.

**Lesson:** When an optional optimization fails, the system should *degrade gracefully*, not slow down. Pattern: probe once, set a flag, fast-fail every subsequent call.

---

## 3. HF Inference API one-call-per-text loop

**Symptom:** Embedding 100 headlines took ~2 minutes.

**Diagnosis:** `task9_embed.py` was looping `for text in texts` and POSTing each one separately. Each round-trip pays TLS handshake + HF model warm-up cost. The actual embedding math is microseconds.

**Fix:** Batch up to 16 texts per request (`json={"inputs": batch}`). Fall back to per-text if a batch fails so one bad input can't poison the whole batch. Same fix in `task8_translate.py` using `translate_batch`.

**Lesson:** Whenever you see a sequential HTTP loop, ask if the API supports a batch payload. Batching collapses fixed per-request overhead.

---

## 4. Sequential adapter fetches in worker

**Symptom:** Cycle time grew linearly with number of sources. With 7 adapters, cycles took 15–25 seconds.

**Diagnosis:** `worker.py` had `for adapter in adapters: adapter.fetch()`. Each fetch is network-bound (waiting on Al Jazeera, BBC, etc.) but the loop blocks on each one before starting the next.

**Fix:** Split the cycle into two phases:
- **Fetch phase** runs all adapters concurrently in a `ThreadPoolExecutor(max_workers=6)`. Python threads are bad for CPU work but great for I/O — when blocked on a socket, the GIL is released.
- **Process phase** still runs serially against one DB connection (avoids pooling complexity, DB writes are fast anyway).

Wrapped each adapter call in `_fetch_one()` so a single source's exception doesn't crash the whole pool.

**Lesson:** I/O-bound = threads. CPU-bound = processes. Always wrap the unit of work submitted to a pool — pool exceptions are easy to lose.

---

## 5. `get_source_map()` queried every batch

**Symptom:** Subtle. Each `write_batch` opened a fresh DB connection just to look up source IDs that never change.

**Fix:** Fetch source map once per cycle in `worker.run_ingestion_cycle()` and pass it down to `write_batch(articles, source_map=source_map)`.

**Lesson:** Cache anything that's stable for the duration of a unit of work.

---

## 6. `Config.validate()` required Telegram credentials

**Symptom:** With `Config.validate()` newly wired into scheduler startup, the worker refused to boot because `TELEGRAM_API_ID` wasn't set — even though Telegram sources are disabled.

**Fix:** Made Telegram a soft warning instead of a hard error. Only `DATABASE_URL` and `NEWSAPI_KEY` are truly required.

**Lesson:** Required-config checks should match what's actually used at runtime, not what the codebase imports.

---

## 7. Free Render Postgres expired

**Symptom:** All connection attempts to the production DB failed with `SSL connection has been closed unexpectedly`. Hostname still resolved (DNS worked), but the DB process was gone.

**Cause:** Render's free Postgres tier expires after ~90 days. The DB had been running since March; this was May.

**Fix:** Migrated the entire database to Supabase free tier (500MB, no expiration). Steps:
1. Created Supabase project in `eu-central-1`
2. Used the **Session pooler** connection URI (port 5432, IPv4 compatible)
3. Enabled `pgvector` extension via Supabase Dashboard → Database → Extensions
4. Ran `migrations/run_migrations.py` against the new URI
5. Updated `DATABASE_URL` env var on both Render services (api + worker)

**Lesson:** Free tiers expire silently. If you're choosing infra for a long-running side project, prefer "free forever with limits" (Supabase, Cloudflare) over "free for N days" (Render Postgres free, Heroku free Redis). Document expiration dates somewhere visible.

---

## 8. Missing schema for NLP tables

**Symptom:** After migrating to fresh Supabase DB and running `001`–`004`, the worker crashed with `column "processed_nlp" does not exist` and `relation "article_pairs" does not exist`.

**Cause:** The NLP pipeline (tasks 8–12) referenced columns and a table that were never in the migration files — they had been added manually to the old Render DB and never committed.

**Fix:** Wrote `migrations/005_nlp_schema.sql` that idempotently adds:
- `articles.processed_nlp BOOLEAN`
- `articles.headline_ar_translated BOOLEAN`
- `article_pairs` table with status / nli_label / contradiction_score columns
- Partial indexes for the "unprocessed" queries

Also rewrote `run_migrations.py` to auto-discover all `NNN_*.sql` files instead of hardcoding the list — so future migrations don't require touching the script.

**Lesson:** If a table or column exists in production but not in source control, you have a ticking time bomb. Audit periodically by spinning up a fresh DB and running migrations cleanly.

---

## 9. Embedding dimension mismatch — `expected 768 dimensions, not 384`

**Symptom:** `task9_embed` ran cleanly through the HF API (multiple `200 OK` logs), then crashed when inserting embeddings into Postgres:

```
psycopg2.errors.DataException: expected 768 dimensions, not 384
```

**Cause:** Schema declared `embedding vector(768)`, but `paraphrase-multilingual-MiniLM-L12-v2` produces 384-dim vectors. The schema was wrong, not the model.

**Fix:** Migration 006 drops the column + HNSW index and recreates them as `vector(384)`. Updated `001_create_tables.sql` to the correct dimension for fresh setups.

**Lesson:** When you pick a model, write its output dimension on a Post-it. Vector DB schemas can't be implicitly resized.

---

## 10. NLI returning `neutral` with score 0.0 for every pair

**Symptom:** All `article_pairs` got `nli_label = neutral` and `contradiction_score = 0.0000`. Exactly zero — not low values like 0.05 or 0.1, but exactly 0.

**Diagnosis:** `task11_nli.py` was using `bart-large-mnli` via the **zero-shot-classification** pipeline:
```python
{
  "inputs": "premise [SEP] hypothesis",
  "parameters": {"candidate_labels": ["contradiction", "neutral", "entailment"]}
}
```

Zero-shot asks "is this concatenated text *about* the topic 'contradiction'?", which is a completely different question from "do these two sentences contradict each other?". The pipeline was wrongly framed.

**Fix:** Switched to `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` — a true NLI model that supports Arabic + English natively. Now passing premise and hypothesis with the XNLI separator format `premise</s></s>hypothesis`.

Also switched the input order in `run_task11` to prefer Arabic headlines when both exist (the model is multilingual; using Arabic preserves more signal than translating to English first).

**Lesson:** The HF "Inference API" hides which pipeline a model uses by default. Read the model card carefully — many NLI-trained models default to zero-shot-classification on the API which is not what you want for true NLI.

---

## 11. HF auto-routing to wrong pipeline → 400 Bad Request

**Symptom:** After switching to mDeBERTa, every NLI call returned:
```
HTTP 400: zero-shot-classification expects `inputs` to be either a string
or a dict containing the key `text` or `sequence`
```

**Cause:** HF Inference's router defaults this model to zero-shot-classification (the model's primary trained task). Our payload was the text-classification format, so the router rejected it.

**Fix:** Forced the text-classification pipeline by appending `/pipeline/text-classification` to the URL, and added `"parameters": {"top_k": null}` to get scores for all 3 NLI classes instead of just the winning one.

**Lesson:** When using the HF Inference router, always specify the pipeline explicitly. The default may not be what you expect.

---

## 12. Conflict scoring formula crushed equal-trust contradictions

**Symptom:** NLI was producing strong contradiction scores (0.99, 0.92, 0.66), but `conflicts` table stayed empty.

**Diagnosis:** `task12_conflicts.py` had:
```python
conflict_score = contradiction_score * trust_diff * max_trust
```

When two equal-trust sources contradict (Al Jazeera 1.0 vs AP 0.8 → trust_diff = 0.2), the score gets crushed to ~0.06 — below the 0.10 threshold. The formula was designed to reward "high-trust contradicts low-trust" pairs, which is actually backwards: an Al Jazeera vs Reuters disagreement is *more* interesting, not less.

**Fix:** Replaced the formula with `contradiction_score * max_trust`. Rewards contradictions where at least one source is trustworthy. Bumped `CONFLICT_SCORE_THRESHOLD` from 0.10 to 0.15 (then later to 0.30) to compensate.

**Lesson:** Question every "weighted score" formula. Write down what each multiplicand does and ask if it actually reflects your intent.

---

## 13. Render free tier nuances

A few quirks worth knowing:

- **Free web services spin down after 15 min of inactivity.** First request after spin-down takes 50+ seconds. Tell users.
- **`type: worker` has no HTTP server** — Render sees the port unbound and times out the deploy. Use `type: web` even for "worker" services and bind a dummy HTTP port (the scheduler does this with `start_health_server`).
- **Auto-deploy on `git push`** is enabled by default. Manual deploy via dashboard skips webhook latency but build still takes ~2 min on free tier.
- **Health check path** in service settings should be empty unless you actually have a `/health` endpoint that responds quickly. A health check pointed at a slow endpoint will keep the service in "Deploying" forever.

---

## How to use this doc

When something breaks, search this file before debugging. Even if your symptom doesn't match exactly, you'll often find a similar shape — and the "Lesson" line tells you the general pattern to look for.

Append new entries here as they happen. Each one should answer: *what did it look like, what was actually broken, what fixed it, and what's the takeaway.*

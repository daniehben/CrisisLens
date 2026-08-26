# Decision Trail — Phase 1

**Date:** 2026-08  
**Scope:** Every micro-decision, question, and course-correction that shaped Phase 1 — the context behind the code, not the code itself.  
**Read alongside:** `changes/01-phase1-reliability-quality.md` (what changed) and `audits/source-audit-2026-08.md` (source test results).

The format for each entry is: **what the user observed or asked → what that triggered → what the outcome was.**

---

## 1. "What are better fallback methods for images?" → task6 topic fallback system

The original task6 (`task6_images.py`) left `image_url = ''` permanently when an og:image fetch failed. This showed up in the frontend as blank grey boxes — frequent because many sources (particularly those that run on Cloudflare or block datacenter IPs) return 403s or empty meta tags to Render's Frankfurt egress address.

**Question triggered:** Is there a structured way to assign contextually relevant images even when the source blocks us?

**Investigation:** Looked at which source codes were landing blank most often, then mapped each source to one of four editorial topic buckets: `conflict`, `politics`, `humanitarian`, `news`. Each bucket maps to a stable Unsplash CDN URL that stays editorially consistent (e.g. conflict sources get a wire-photo-style war-zone image rather than a stock business photo).

**Outcome:**
- `SOURCE_TOPIC` dict added to `task6_images.py` covering 37 source codes
- `TOPIC_FALLBACK_IMAGES` dict maps each topic to a CDN URL
- WHERE clause extended to backfill existing `''` sentinels — thousands of already-ingested articles were stuck with the empty string and would never be retried without this
- **Known gap carried forward:** PCH and IMEMC (added later in the audit phase) are not in `SOURCE_TOPIC` yet — they fall through to a generic fallback rather than a topic-specific one

---

## 2. "Can't we increase the 384-dimension vector?" → 768-dim upgrade + schema migration

The conflict detection engine works by computing cosine similarity between article embeddings. At 384 dimensions, false positives were occurring: headlines that sounded similar but had different framings were landing closer together than they should (e.g. "Israel says ceasefire agreed" and "Hamas says no ceasefire" ended up near each other in vector space because they share vocabulary).

**Question triggered:** Does Jina support higher-dimension output without switching models?

**Investigation:** Jina's `jina-embeddings-v3` supports MRL (Matryoshka Representation Learning) truncation — you can request up to 1024 dimensions with a single API parameter change. 768 was chosen as the sweet spot: enough extra resolution to separate same-event/different-claim pairs without the storage and index cost of 1024.

**Outcome:**
- `EMBEDDING_DIM` changed from 384 → 768 in `task9_embed.py`
- Schema migration added to `scheduler.py`: runs at every worker startup, inspects `pg_attribute` for column type size, drops-and-recreates the column + HNSW index if still 384-dim, resets `processed_nlp = FALSE` so all existing articles get re-embedded
- Summaries-only filter added alongside this: articles now wait for a Groq-generated summary before being embedded, because `body_snippet` (raw first 500 chars of RSS feed) frequently contains HTML artifacts, bylines, and footer boilerplate that degrades embedding quality

---

## 3. "Let's address the Jina budget issue" → CircuitBreaker for both Jina and Groq

Jina and Groq are paid external APIs. Without a backoff mechanism, a temporary outage (Groq having a 30-minute incident, Jina rate-limiting on a large batch) causes the worker to hammer the failing endpoint every 15 minutes around the clock — burning quota, generating hundreds of error log lines, and potentially incurring cost on a degraded service.

**Question triggered:** What's the right pattern for protecting paid API calls?

**Investigation:** Standard circuit breaker pattern: CLOSED (normal) → OPEN (stop calling after N failures) → HALF_OPEN (single probe after cooldown). Already common in backend systems; no dependency needed — implemented in ~50 lines in `backend/shared/circuit_breaker.py`.

**Thresholds chosen:**
- Groq: `failure_threshold=5, cooldown_s=300` — lenient because Groq is called at high volume per cycle; tripping on 5 failures avoids false positives from transient errors
- Jina: `failure_threshold=3, cooldown_s=180` — tighter because a single bad Jina batch blocks the entire NLP pipeline

**Outcome:**
- `circuit_breaker.py` added as new shared module
- Groq client and Jina task9 both wired to check `allow()` before calling, and call `record_success()` / `record_failure()` depending on outcome
- Health endpoint upgraded: was `{"status": "worker running"}` (confirmed process alive, nothing else). Now returns Groq usage (call counts vs daily caps per model) and circuit breaker state snapshots — a worker with an open circuit breaker looks identical to a healthy one in the old response

---

## 4. "I want to audit the sources deeper" → full source routing + feed audit

Several sources were either silently returning 0 articles or returning content that wasn't editorially useful. This came up after looking at ingestion logs and noticing some source codes had low article counts relative to their expected output.

**Question triggered:** Which sources are actually delivering? Where are the failure modes?

**Investigation:** Traced each low-count source back to its URL. Found several patterns:
- Direct feeds returning 403 from Render Frankfurt IPs (F24, ARB)
- Direct feeds returning 404 after CMS migrations (MAYE, SDT)
- Source not indexed by Google News at all (WAF direct → dead; MAN)
- AJA+ Telegram bridge hitting rate limits (HTTP 429 on every cycle, 0 articles landing)
- AJA (Al Jazeera Arabic) was pointed at the English feed — hours-behind, less regional coverage, the same content BBC/Reuters already covers

**Outcome per source:**
- **F24, ARB:** switched to Google News proxy — bypasses IP block
- **MAYE, SDT:** direct feeds dead; switched to Google News proxy
- **WAF:** moved to Google News proxy (direct 404)
- **AJA+:** disabled — Telegram RSSHub bridge rate-limited on every cycle, consuming a concurrency slot for 0 articles. Flagged in `BACKLOG.md` for re-enable when a paid RSSHub instance is available
- **AJA:** switched from `aljazeera.net/en` to `aljazeera.net/rss/all.xml` — Arabic is platform-primary; the Arabic feed publishes hours ahead of the English edition, covers stories that never get translated, and AJA's trust weight (0.90) was being wasted on content already covered by three other sources

---

## 5. "As my main focus is to put CrisisLens out soon" → four-tier source prioritization framework

With the source list expanding and some sources clearly more reliable than others, the question became: if I can only deploy a subset confidently, which ones matter most?

**Question triggered:** Is there a principled way to rank source importance?

**Investigation:** Framed a four-tier framework around what each source type contributes to conflict detection quality:

| Tier | Role | Examples |
|---|---|---|
| Tier 1 — Core conflict signal | Highest-trust, highest-volume; form the backbone of NLP pair generation | AJA (0.90), BBC (0.85), REU (0.85), AP (0.80) |
| Tier 2 — Editorial diversity | Different editorial line from Tier 1; creates the contradiction pairs the engine is built for | JRP (0.70), HAA (0.70), ARB (0.75), TAS (0.55) |
| Tier 3 — Ground-level | Incident reporting that the wires don't cover in granular detail | WAF, IMEMC, PCH, EI |
| Tier 4 — Commentary/context | Lower cadence, higher interpretation; adds framing signal to pair analysis | GG, MEE, CJ |

**Outcome:** This framework was used as the mental model for trust weight assignments on newly added sources. It also shaped the decision to prioritise the source routing fixes (Tier 1/2 sources that were silently failing) before adding new ones.

---

## 6. "Read our chats and see what changes we had planned for Phase 1" (×3 across the session) → scope confirmation

At several points during the session the Phase 1 scope was re-confirmed against earlier planning notes: circuit breaker, health monitoring, 768-dim embeddings, topic fallback images, and the summaries-only filter were all previously discussed but not yet committed. These reviews prevented scope creep and kept the focus on landing the planned reliability changes before expanding the source list.

**Outcome:** Each review confirmed the same four priorities. No new scope was added as a result — the reviews were used to sequence the work, not expand it.

---

## 7. "This doesn't feel in depth" → live local test methodology, caught TNA 0-entry and MAN 0-entry bugs

The initial source audit was desk-based: checking URLs in browser and reviewing documentation. The observation that this wasn't deep enough triggered switching to a live feed test using the exact same `httpx` + `feedparser` stack the worker runs, with browser headers matching the worker's UA string.

**Question triggered:** Are these sources actually returning articles when called the way the worker calls them?

**Investigation:** Ran the test script locally (not in cloud sandbox — Anthropic egress restrictions block outbound HTTP to news sites from the sandbox). Results revealed:
- **TNA (The New Arab):** original URL `thenewsarab.com` returns HTTP 200 but **0 entries** — the domain is not indexed by Google News. The actual domain is `newarab.com`. Corrected URL returns **100 entries**.
- **MAN (Ma'an News Agency):** `maannews.com` not indexed by Google News across all geo/hl variants; direct RSS returns 403. Deactivated.

Without the live test, both of these bugs would have survived into production — TNA wasting a concurrency slot for 0 articles every cycle, MAN the same.

**Outcome:**
- TNA URL corrected in `rss_adapter.py` and DB `feed_url`
- MAN deactivated: removed from `rss_adapter.py` and `worker.py`, DB row set `is_active = FALSE`

---

## 8. "Before committing, what does dropping MAN mean?" → coverage gap analysis → PCH + IMEMC added

Before removing MAN from the adapter list, the decision to pause and analyze the gap it left prevented a silent editorial regression. MAN occupied a specific intersection of properties: independent of PA, West Bank ground-level, factual wire tone. No single existing source covered all three simultaneously.

**Question triggered:** What specifically did MAN cover that WAF and MND/EI don't?

**Investigation:**

| Property | WAF | MND / EI | MAN |
|---|---|---|---|
| Independent of PA | ❌ (PA official) | ✅ | ✅ |
| West Bank ground-level | ✅ | ❌ (US-based) | ✅ |
| Wire/factual tone | ✅ | ❌ (advocacy) | ✅ |

Two replacement candidates identified and tested:
- **PCH (Palestine Chronicle):** 100 entries, US-registered, publishes West Bank correspondents, analytical frame. Assigned trust 0.55 (slightly more interpretive than a pure wire service).
- **IMEMC:** 100 entries, physically based in Bethlehem, 20+ year track record of incident-level West Bank reporting (specific villages, checkpoints, military units). Assigned trust 0.60 — closest functional replacement for MAN's factual/wire style.

**Outcome:** Both added to all three files (`rss_adapter.py`, `worker.py`, `main.py`). The gap analysis also led to documenting PCH + IMEMC as missing from `task6_images.py` SOURCE_TOPIC — a known gap carried forward.

---

## 9. ON CONFLICT override issue → MAN disable block fix

When removing MAN, the initial approach was to set `is_active = FALSE` in the VALUES row of the startup upsert. This wasn't enough.

**Problem discovered:** The ON CONFLICT clause for the startup upsert hardcodes `is_active = TRUE` in the DO UPDATE SET. This means: even if the VALUES row says `FALSE`, the ON CONFLICT path always writes `TRUE`, silently re-activating any source that already exists in the DB.

**Fix:** Added MAN (alongside AJE and BBC+) to the post-upsert disable block — a separate `UPDATE sources SET is_active = FALSE WHERE code IN (...)` that runs after the upsert and wins.

**Lesson:** Any source that should stay inactive must be in the disable block, not just in the VALUES row, because of how the ON CONFLICT path works.

---

## 10. "HAA missing from DB entirely" → pre-deploy Supabase seed

Running a pre-deploy verification SELECT against Supabase showed HAA was not in the results at all. The API server had a code change adding HAA to the startup upsert, but it had never deployed — so the DB row had never been created.

**Problem:** If the worker deployed first and tried to ingest HAA articles before the API server created the HAA row, HAA would silently fall through `get_source_map()` and articles would be dropped.

**Fix:** Inserted HAA directly in Supabase before the git push. Also fixed ASH's stale `feed_url` (DB still showed the old direct `aawsat.com/feed` URL instead of the Google News proxy URL the worker was actually using).

---

## 11. Deploy race condition identified → Supabase pre-seeding as standard practice

API server and worker deploy in parallel on Railway/Render. The API server creates source rows at startup; the worker starts its first ingestion cycle at startup. If the worker fires first, source rows for any newly added sources don't exist yet in `source_map`, and articles from those sources are silently dropped — no error, no log entry, just missing data.

**Decision:** Pre-seed all new source rows directly in Supabase before git push. Neither service has anything to race against — the rows exist before either boots.

**Outcome:** Pre-deploy SQL block added to the session workflow. Going forward, any new source addition should follow: (1) write code, (2) run INSERT directly in Supabase, (3) push git. The change log and this document capture that as a standing practice.

---

## Standing Practices Established in Phase 1

- **New source = test first locally**, using `httpx` + `feedparser` with browser headers, minimum 20-entry threshold
- **New source = pre-seed in Supabase before push** to eliminate API server/worker race condition
- **Inactive source = disable block, not just VALUES** — ON CONFLICT always writes `is_active = TRUE`
- **New source = add to `task6_images.py` SOURCE_TOPIC** — articles without a topic mapping get a generic fallback image
- **Phase changes = numbered change log** in `docs/changes/` — each log names files changed so future phases can read context before touching the same code

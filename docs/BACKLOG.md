# CrisisLens — Backlog & Deferred Work

Things we know we want to do, but explicitly aren't doing right now. Each entry says **what** the work is, **why we're not doing it yet**, **what condition triggers picking it up**, and **roughly how long** it'll take.

The order matters: items at the top are the next ones to grab when their trigger fires.

---

## 1. Re-enable AJ Plus Arabic (AJA+)

**What:** Re-wire the AJA+ Telegram-channel feed currently disabled in `rss_adapter.py` and `worker.py`.

**Why deferred:** Our self-hosted RSSHub bridge (`crisislens-rsshub.onrender.com`) hits Telegram's rate limit and returns HTTP 429 every cycle. The bridge runs on Render free tier, which means low CPU + a shared egress IP that Telegram throttles aggressively. We can't fix the rate limit without either changing infra or fetch frequency.

**Why we still want it:** AJ Plus Arabic is a major bilingual source for MENA youth audiences. It often runs counter-narrative coverage of Israel/Palestine that doesn't appear in mainstream Western sources, which is exactly the kind of cross-source asymmetry CrisisLens is built to surface.

**When to pick this up:** As soon as any one of these is true:
- We move RSSHub off Render free tier (Render Starter $7/mo, or self-host on a $5 Hetzner box)
- We migrate to Telegram **Bot API** (HTTPS, not MTProto) — bypasses the rate-limit issue entirely but only works for public channels that have allowed the bot
- The Render-free RSSHub instance gets stable for 48+ hours of cycles (unlikely without paid hosting)

**Estimated effort:** 30 min if we move RSSHub to a paid host. 60 min if we go via Bot API (need to register the bot, get the channel info, write a small adapter). Either way, low risk.

---

## 2. Telegram source unlock (BNO, AJE+, REU, BBC+, WM, SI)

**What:** Re-enable the 6 Telegram channels currently commented out in `worker.py`. They're already wired up via `telegram_adapter.py` using Telethon (MTProto).

**Why deferred:** Render Frankfurt IPs cannot complete the MTProto TLS handshake — Telegram blocks the egress range. The Telethon adapter is wired but commented out.

**Why we still want it:** This unlocks 6 high-quality sources at once: Reuters (Telegram), BBC Breaking, AJE on Telegram, BNO News (real-time breaking news aggregator), and two lower-trust signal sources (War Monitor and Spectator Index). For a real-time crisis aggregator, BNO + Reuters + BBC Breaking via Telegram is the gold standard — these channels post within minutes of an event.

**When to pick this up:** This is the **single highest-value source-expansion task remaining**. Pick it up once we've validated NLP precision on the existing 8+ sources (target: precision ≥ 60% on a 30-pair sample). Telegram unlock multiplies pair-volume 3-5x — only worth it once the model isn't producing junk.

The fix is to switch from Telethon (MTProto) to the **Telegram Bot API** (HTTPS, never blocked). Workflow:
1. Talk to `@BotFather` on Telegram, create a bot, get its token
2. Add the bot to each public channel as an admin (or use channel exports for channels that don't allow bots)
3. Rewrite `telegram_adapter.py` to call `https://api.telegram.org/bot<TOKEN>/getUpdates` instead of using Telethon

**Estimated effort:** 60–90 min including BotFather setup and one-pass test against each channel.

---

## 3. RSSHub bridges for Asharq Al-Awsat (ASH) and The New Arab (TNA)

**What:** Wire two more Arabic-language sources currently blocked at the source: `aawsat.com` (Asharq) and `newarab.com` (The New Arab) both return 403 to Render Frankfurt IPs.

**Why deferred:** Same blocking mechanism as ARB. We've already routed ARB through Google News RSS as a proof of concept. The same pattern works for ASH/TNA but each one needs its own Google News query crafted (different language hints, different gl/hl/ceid params for best Arabic-language results).

**Why we still want it:** ASH (Saudi establishment view) and TNA (London-based pan-Arab editorial) round out the Arabic-language perspective spectrum. Combined with AJA (Qatari), ARB (Saudi via Google News), DW (German state), F24 (French state), and AJ+ (when re-enabled), we'd cover most of the Arabic news perspective grid.

**When to pick this up:** As soon as we want to push past 8 sources. Should follow shortly after Telegram unlock.

**Estimated effort:** 20 min — basically copy the ARB Google News URL pattern and adjust the `q=site:` parameter.

---

## 4. Same-story collapse (NLP precision fix)

**What:** Detect when two articles in a candidate pair are actually about the *same event* with different framing, rather than contradicting each other. Filter those out before they become "conflicts."

**Why deferred:** It's a real improvement to NLP precision but requires some non-trivial design work — entity extraction, entity overlap scoring, threshold tuning — and we want to validate the existing pipeline at higher data volume first. With only 5 conflicts labeled (40% precision), any tuning is overfitting.

**Why we still want it:** This is the #1 cause of false positives in the labeled set. Three of five false positives (#1, #3, #5) are "same story, different angle." Threshold tuning alone can't fix this — these pairs come back with high contradiction confidence (0.66, 0.92, 0.50) because the model genuinely sees surface-level differences in framing as contradiction.

**When to pick this up:** After we have 30+ labeled conflicts (so tuning isn't overfit) AND after Telegram unlock (so we have a representative source mix). Probably 2–3 weeks of cycle accumulation away.

**Implementation sketch:**
- Extract named entities from both headlines using either spaCy multilingual (heavy: ~500MB) or `dslim/bert-base-NER` via HF Inference (light, free)
- Compute entity Jaccard overlap: `|entities_a ∩ entities_b| / |entities_a ∪ entities_b|`
- If overlap ≥ 0.7 AND similarity ≥ 0.85, mark as "same_story" instead of inserting into conflicts
- Optional: surface "same_story" pairs in a separate UI section as "5 sources covering this event"

**Estimated effort:** 2–3 hours including the entity extraction wiring and threshold tuning.

---

## 5. Numeric mismatch detector (NLP precision boost)

**What:** When both headlines contain numbers AND the numbers don't overlap (10 vs 7, $5B vs $10B, 50 dead vs 100 dead), strongly boost the conflict score and surface this as a primary signal.

**Why deferred:** Highest precision-boost we know of, but only useful once we have enough conflicts that we want to *rank* them by interestingness. With 3 conflicts in the table, ranking is moot.

**Why we still want it:** This is the killer-app signal. The "10 vs 7 dead in Lebanon" example is exactly what makes a CrisisLens screenshot go viral. Numeric disagreements between equally-trusted sources are the strongest signal of newsworthy contradiction.

**When to pick this up:** Right after same-story collapse (#4). They're naturally complementary — same-story collapse catches the false positives, numeric mismatch catches the strongest true positives.

**Implementation sketch:**
- Regex extract all numbers from both headlines (`re.findall(r'\b\d+(?:[.,]\d+)?\b', headline)`)
- Normalize Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) to Arabic digits
- If both have numbers AND no number from set A appears in set B (within tolerance), set a `numeric_disagreement` flag
- In the conflict scoring, multiply weight by 1.5 when `numeric_disagreement = True`
- In the frontend, render a 🔢 badge on these conflicts

**Estimated effort:** 1–2 hours, low risk.

---

## 6. Event clustering (UI quality + analytics)

**What:** Cluster articles into "events" — groups of articles all covering the same incident — using the existing `events` table that's defined but unused. Surface in the UI as "8 sources covering this story" with the conflict view as a sub-section.

**Why deferred:** Significant UX redesign, requires real entity extraction (#4 dependency), and we want to first prove the pairwise-contradiction primitive carries the product before layering event clustering on top.

**Why we still want it:** Pairwise conflicts get repetitive — if 5 sources all report on the same Lebanon strike, you get 10 pair combinations and 6 of them might be contradiction-flagged. Event clustering collapses these into one "Lebanon strike" event with a contradiction count, which is a much more powerful unit for the UI.

**When to pick this up:** Phase 4 work, after Phase 3 (current frontend) is validated by real users and we have feedback that "this gets repetitive."

**Estimated effort:** 4–6 hours — clustering algorithm, UI rework, event detail page.

---

## 7. Authentication + per-key rate limits on the API

**What:** Move from open `/api/v1/*` to API-key-gated, with per-key quotas in Redis (when Redis works).

**Why deferred:** No traffic yet. Premature optimization.

**Why we still want it:** As soon as anyone discovers the URL, the free-tier API will get hammered and burn the NewsAPI quota indirectly (via what other people query). Also blocks any future B2B revenue path.

**When to pick this up:** When monthly API requests > 10K, OR when launching publicly to a non-trivial audience.

**Estimated effort:** 3 hours with Supabase Auth (which is already set up — just unused).

---

## 8. Observability dashboard

**What:** Build a `/api/v1/admin/health` page showing per-source ingestion success rates, NLP task latencies, and recent errors from `ingestion_logs`. Read-only, behind admin auth.

**Why deferred:** Right now we read worker logs in the Render dashboard, which is fine for one engineer. A proper dashboard becomes valuable when there are multiple engineers OR when problems get harder to spot in raw logs.

**Why we still want it:** The `ingestion_logs` table already exists and is being written to on every cycle. We just don't have a UI on it. With 10+ sources, eyeballing logs for "which one stopped working last Tuesday" is painful.

**When to pick this up:** When source count > 12, OR when first non-founder is involved in keeping the system running.

**Estimated effort:** 4 hours including the SQL queries, the page, and basic auth.

---

## 9. Test coverage

**What:** Add unit tests for the NLP tasks, integration tests for the full pipeline, and contract tests for the API endpoints.

**Why deferred:** With one engineer iterating fast, test maintenance cost > test value. Right now changes are validated by spot-checking the labeling CSV.

**Why we still want it:** As soon as we're tuning thresholds based on labeled data, regression tests catch precision drops we'd otherwise miss. Also: any time we restart the project after a break, tests are the cheapest way to know what still works.

**When to pick this up:** Before the first non-founder commit. Or when the labeled-conflict set hits 100+ examples (then we have a real "fixture" to test against).

**Estimated effort:** 1 day for a meaningful first pass.

---

## 10. Move off Render free tier (or to Cloudflare Workers)

**What:** Either pay Render for Always-On services (no spin-down) or migrate to Cloudflare Workers for the API.

**Why deferred:** Free tier works. The 50-second cold start is a UX problem only when traffic is rare. With regular traffic, the service stays warm.

**Why we still want it:** Cold start is a brutal first impression for any user clicking a shared link to the site. Either pay $7/mo per service for always-on, or migrate to a CDN-edge architecture.

**When to pick this up:** When we want to publicly launch (Twitter, ProductHunt, etc.). The first impression matters a lot for that.

**Estimated effort:** Zero if we just upgrade the Render plan. ~1 day if we migrate to Cloudflare.

---

## How to use this list

When you have time and don't know what to work on, scan from the top. The first item whose **trigger condition** is met is the one to pick. If multiple triggers are met, prefer the smallest-effort item — momentum beats optimization.

When you complete an item, move it to `CHANGELOG.md` and delete it here.

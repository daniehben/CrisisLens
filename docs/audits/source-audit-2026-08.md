# Source Audit — August 2026

**Date:** 2026-08-26  
**Scope:** 4 newly activated sources (HAA, TNA, ASH, MAN) + replacement candidates  
**Method:** Live feed test from local machine using the exact same RSSAdapter code the worker runs — `httpx` with browser headers, `feedparser` parse, entry count + headline sample. Anthropic cloud sandbox has egress restrictions that block all outbound HTTP to news sites, so all testing was done locally.

---

## Test Methodology

```python
import httpx, feedparser

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Accept-Language': 'ar,en;q=0.9',
}

# For each source: httpx GET → feedparser.parse(response.content) → count entries
```

Entry count threshold: **20+ entries = viable**. Below that the source is either blocked, not indexed, or too low volume to contribute meaningful conflict signal.

---

## Initial Audit Results

### HAA — Haaretz (English)
- **URL tested:** `https://news.google.com/rss/search?q=site:haaretz.com&hl=en&gl=IL&ceid=IL:en`
- **Result:** ✅ HTTP 200, **50 entries**
- **Sample headlines:** Israeli left-liberal reporting; frequently contradicts JRP on same events
- **Decision:** Active, no changes needed

### ASH — Asharq Al-Awsat (Arabic)
- **URL tested:** `https://news.google.com/rss/search?q=site:aawsat.com&hl=ar&gl=SA&ceid=SA:ar`
- **Result:** ✅ HTTP 200, **50 entries**
- **Sample headlines:** Pan-Arab editorial, high quality, editorially distinct from ARB
- **Decision:** Active, no changes needed

### TNA — The New Arab (English)
- **URL tested (original):** `https://news.google.com/rss/search?q=site:thenewsarab.com&hl=en&gl=GB&ceid=GB:en`
- **Result:** ❌ HTTP 200 but **0 entries** — `thenewsarab.com` not in Google News index
- **Fix tested:** `https://news.google.com/rss/search?q=site:newarab.com&hl=en&gl=GB&ceid=GB:en`
- **Result after fix:** ✅ HTTP 200, **100 entries**
- **Decision:** Domain corrected to `newarab.com` in `rss_adapter.py` and DB `feed_url`

### MAN — Ma'an News Agency (English)
- **URL tested (primary):** `https://news.google.com/rss/search?q=site:maannews.com&hl=en&gl=PS&ceid=PS:en`
- **Result:** ❌ HTTP 200 but **0 entries**
- **Alternatives tested:**
  - `site:maannews.net` (alternate domain) → 0 entries
  - `site:maannews.com` with `gl=US` → 0 entries
  - `site:maannews.com` with `gl=GB` → 0 entries
  - Direct RSS (`maannews.com/rss`) → HTTP 403
- **Root cause:** `maannews.com` is not indexed by Google News. Direct feed actively blocks scrapers.
- **Decision:** Deactivated — removed from `rss_adapter.py` and `worker.py`, DB row set `is_active = FALSE`

---

## Coverage Gap Analysis — MAN Removal

MAN represented a specific intersection of three properties no other source in the list provided simultaneously:

| Property | WAF | MND / EI | MAN |
|---|---|---|---|
| Independent of PA | ❌ (PA official) | ✅ | ✅ |
| West Bank ground-level | ✅ | ❌ (US-based) | ✅ |
| Wire/factual tone | ✅ | ❌ (advocacy) | ✅ |

Without MAN, the pipeline retained Palestinian *official* coverage (WAF) and Palestinian *advocacy* coverage (MND, EI) but lost independent, factual, West Bank-filed reporting.

---

## Replacement Candidates Tested

### PCH — Palestine Chronicle
- **URL tested:** `https://news.google.com/rss/search?q=site:palestinechronicle.com&hl=en&gl=PS&ceid=PS:en`
- **Result:** ✅ HTTP 200, **100 entries**
- **Sample headlines:**
  - *'They Have No Rights': Israel's Chief Rabbi Denies Existence of Palestinian People*
  - *Father of Two Slain Palestinians Killed by Israeli Forces in Jenin*
  - *Stay Out of E1: Palestine Warns Firms against Israeli Settlement Tenders*
- **Profile:** US-registered but publishes West Bank correspondents. Analytical/contextual frame. Independent.
- **Trust weight assigned:** 0.55
- **Decision:** ✅ Added

### IMEMC — International Middle East Media Center
- **URL tested:** `https://news.google.com/rss/search?q=site:imemc.org&hl=en&gl=PS&ceid=PS:en`
- **Result:** ✅ HTTP 200, **100 entries**
- **Sample headlines:**
  - *Israeli Colonizer Attacks Escalate Across the West Bank*
  - *Hebron: Colonizers Kill a Palestinian Child, Injure an Elderly Man*
  - *UNRWA Denies False Claims on Qalandia Training Center*
- **Profile:** Physically based in Bethlehem. Joint international-Palestinian project. Incident-level West Bank reporting — specific villages, checkpoints, military units. Closest functional replacement for MAN.
- **Trust weight assigned:** 0.60
- **Decision:** ✅ Added

---

## Final Source Roster Changes

| Code | Action | Reason |
|---|---|---|
| HAA | ✅ No change | Working, 50 entries |
| ASH | ✅ No change | Working, 50 entries |
| TNA | 🔧 Domain fix | `thenewsarab.com` → `newarab.com` |
| MAN | ❌ Deactivated | Not in Google News index; direct feed 403 |
| PCH | ➕ Added | 100 entries; fills MAN's independent West Bank slot |
| IMEMC | ➕ Added | 100 entries; incident-level West Bank reporting |

---

## Files Changed

- `backend/ingestion_worker/adapters/rss_adapter.py` — TNA URL corrected; MAN entry removed; PCH + IMEMC entries added
- `backend/ingestion_worker/worker.py` — MAN removed from adapter list; PCH + IMEMC added to Palestinian section
- `backend/api_server/main.py` — TNA feed_url corrected; MAN set `is_active=FALSE` in disable block; PCH + IMEMC rows added to upsert and SOURCE_PROFILE

---

## DB Pre-Deploy Verification

Ran directly in Supabase before deploying to eliminate API server / worker boot race condition:

```sql
-- Insert PCH, IMEMC; fix TNA feed_url; deactivate MAN; fix ASH stale feed_url
-- HAA inserted separately (was missing from DB pre-deploy)
```

Final pre-deploy state confirmed:

| code | is_active |
|---|---|
| ASH | true |
| HAA | true |
| IMEMC | true |
| MAN | false |
| PCH | true |
| TNA | true |

---

## Post-Deploy Verification SQL

```sql
-- Confirm articles landing for all 5 active new sources
SELECT s.code, COUNT(a.article_id) AS articles, MAX(a.fetched_at) AS last_fetch
FROM sources s
LEFT JOIN articles a ON a.source_id = s.source_id
WHERE s.code IN ('HAA', 'TNA', 'ASH', 'PCH', 'IMEMC')
GROUP BY s.code
ORDER BY s.code;
-- All 5 should have articles > 0 after first cycle post-deploy

-- Confirm no ingestion errors for new sources
SELECT s.code, il.status, il.articles_fetched, il.articles_new, il.run_at
FROM ingestion_logs il
JOIN sources s ON s.source_id = il.source_id
WHERE s.code IN ('HAA', 'TNA', 'ASH', 'PCH', 'IMEMC')
ORDER BY il.run_at DESC
LIMIT 15;
-- All rows should show status = 'ok'
```

# CrisisLens — Deployment Prerequisites

> This document tracks everything that must be completed, validated, or decided before CrisisLens goes public.
> Work through each section in order. Check off items as done.
> Last updated: 2026-06-03

---

## How to use this document

Each section has a **status** at the top: 🔴 Blocked / 🟡 In Progress / ✅ Done.
Each item has a checkbox. Check it when verified — not when you think it's done.

---

## Section 1 — Domain & Hosting

**Status: 🟡 In Progress — running on placeholder Vercel URL**

- [ ] **Decide on final domain name** before any public sharing. Once shared widely, changing it breaks links.
- [ ] **Purchase domain** (Namecheap, Cloudflare Registrar, or Porkbun recommended — all ~$10/year for .com)
- [ ] **Add domain to Vercel**
  - Vercel dashboard → Project → Settings → Domains → Add
  - Point DNS: add Vercel's A record and CNAME at your registrar
  - Wait for SSL certificate (usually < 5 minutes on Vercel)
- [ ] **Update `ALLOWED_ORIGINS` in Render dashboard**
  - crisislens-api → Environment → `ALLOWED_ORIGINS`
  - Set to: `https://yourdomain.com,https://www.yourdomain.com`
  - Trigger manual redeploy of crisislens-api after saving
- [ ] **Find and replace placeholder URL across three files:**
  - `frontend/index.html` — canonical, og:url, og:image, twitter:image, JSON-LD url
  - `frontend/robots.txt` — Sitemap URL
  - `frontend/sitemap.xml` — `<loc>` entry
  - Search for: `crisis-lens-six.vercel.app`
  - Replace with: `yourdomain.com`
- [ ] **Verify domain resolves** with `curl -I https://yourdomain.com` — expect `HTTP/2 200`

---

## Section 2 — SEO Validation

**Status: 🟡 In Progress — tags implemented, not yet validated on live domain**

### 2a. OG Image

The OG image is served as `og-image.svg` via a Vercel rewrite to `/og-image.png`.
Twitter/X requires an actual PNG — the SVG rewrite works for most platforms but should be tested.

- [ ] **Verify OG image loads**
  - Open: `https://yourdomain.com/og-image.png`
  - Expected: CrisisLens branded image, 1200×630px, no broken layout

- [ ] **Convert SVG to real PNG for Twitter compatibility** (Twitter's crawler does not always render SVG)
  - Option A (free, local): Open `frontend/og-image.svg` in a browser → screenshot at 1200×630 → save as `og-image.png`
  - Option B (free, online): Upload SVG to [cloudconvert.com/svg-to-png](https://cloudconvert.com/svg-to-png), set width 1200
  - Option C (free, CLI): `npx sharp-cli -i frontend/og-image.svg -o frontend/og-image.png --width 1200`
  - Place `og-image.png` in `frontend/` alongside the SVG
  - Remove the SVG rewrite from `vercel.json` (the real PNG takes priority)

- [ ] **Update OG image content when domain is finalised**
  - Line in `og-image.svg` (bottom right): `crisis-lens-six.vercel.app` → replace with real domain
  - Regenerate PNG after editing SVG

### 2b. Meta Tags

- [ ] **Validate Open Graph tags** at [opengraph.xyz](https://www.opengraph.xyz)
  - Enter: `https://yourdomain.com`
  - Check: title, description, image all render correctly
  - Check: image is not cropped or blank

- [ ] **Validate Twitter Card** at [cards-dev.twitter.com/validator](https://cards-dev.twitter.com/validator)
  - Enter: `https://yourdomain.com`
  - Expected card type: `summary_large_image`
  - Note: Twitter caches cards aggressively — use `?v=2` if you update tags and need fresh results

- [ ] **Validate WhatsApp/Telegram preview** (most important for your distribution channel)
  - Send `https://yourdomain.com` in a WhatsApp message to yourself
  - Expected: image thumbnail + title + description appear in the link preview
  - If blank: the OG image URL is unreachable or the image dimensions are wrong (must be ≥ 200×200)

- [ ] **Validate LinkedIn** at [linkedin.com/post-inspector](https://www.linkedin.com/post-inspector/)
  - Useful for outreach to journalists and researchers

- [ ] **Add Twitter handle** when account exists
  - Uncomment in `frontend/index.html`: `<meta name="twitter:site" content="@YourHandle">`

### 2c. Structured Data

- [ ] **Validate JSON-LD** at [search.google.com/test/rich-results](https://search.google.com/test/rich-results)
  - Enter: `https://yourdomain.com`
  - Expected: WebSite schema detected, no errors
  - Warnings are acceptable — errors must be fixed

- [ ] **Validate with Schema.org validator** at [validator.schema.org](https://validator.schema.org)
  - Paste the JSON-LD block from `index.html` directly
  - Zero errors required before launch

### 2d. Crawlability

- [ ] **Submit to Google Search Console**
  - Go to [search.google.com/search-console](https://search.google.com/search-console)
  - Add property → Domain → follow DNS verification steps
  - Submit sitemap: `https://yourdomain.com/sitemap.xml`
  - Request indexing of homepage manually via URL Inspection tool

- [ ] **Submit to Bing Webmaster Tools** at [bing.com/webmasters](https://www.bing.com/webmasters)
  - Import from Google Search Console (one-click if GSC is already set up)

- [ ] **Verify robots.txt is accessible**
  - Open: `https://yourdomain.com/robots.txt`
  - Expected: plain text, `Allow: /`, correct `Sitemap:` URL

- [ ] **Verify sitemap is accessible**
  - Open: `https://yourdomain.com/sitemap.xml`
  - Expected: valid XML, `<loc>` matches final domain

- [ ] **Check Google can render the page** (JS rendering validation)
  - Google Search Console → URL Inspection → `https://yourdomain.com`
  - Click "Test Live URL" → "View Tested Page" → "Screenshot"
  - Expected: conflicts are visible in the screenshot (not a blank page)
  - If blank: Google's renderer couldn't execute the JS — this needs investigation

### 2e. Page Speed

- [ ] **Run PageSpeed Insights** at [pagespeed.web.dev](https://pagespeed.web.dev)
  - Enter: `https://yourdomain.com`
  - Target: Performance ≥ 70 mobile, ≥ 85 desktop
  - Font loading (`display=swap` already set) and image lazy loading are the biggest levers
  - Note: Render cold-start will cause a timeout on the API call — this affects Time to Interactive but is expected on free tier

---

## Section 3 — API & Backend

**Status: 🟡 In Progress**

### What was done and why

The worker service crashed repeatedly on Render's free tier due to an out-of-memory kill. PyTorch + the sentence-transformers L12 embedding model peaked at ~580MB — above Render's hard 512MB limit. Two fixes were applied: the embedding model was downgraded from L12 to L6 (identical vector output, ~80MB vs ~480MB weights), and an explicit `release_model()` call was added to the scheduler so the model is freed from RAM immediately after task9 completes rather than staying loaded for the rest of the pipeline. Despite these fixes, the combination of PyTorch's own import overhead (~250MB) and the L6 model left insufficient headroom on Render's 512MB hard limit.

The worker was migrated to Railway on 2026-06-06. Railway throttles on memory pressure rather than hard-killing, and its free trial ($4.92, 28 days) covers the transition period. The API remains on Render free tier — it has no local ML models and fits comfortably within 512MB. The Render worker service has been suspended (not deleted) as a fallback.

All env vars required by the worker (`DATABASE_URL`, `GROQ_API_KEY`, `ARTICLE_RETENTION_DAYS`, `PYTHONUNBUFFERED`) are set in Railway's Variables tab. `TELEGRAM_SESSION_B64` was confirmed unnecessary — the live worker uses `TelegramWebAdapter` which scrapes `t.me/s/` public pages with no auth session.

---

- [ ] **Confirm `ALLOWED_ORIGINS` is set** (see Section 1)
- [ ] **Confirm API is live**
  - `https://crisislens-api.onrender.com/health` → `{"db":"ok",...}`
- [ ] **Confirm Railway worker is live**
  - Railway dashboard → worker service → logs → confirm `[scheduler] Running initial ingestion cycle...` present and no crash
- [ ] **Confirm article count is growing**
  - Check `/health` response `articles_count` at two points 15 minutes apart
  - If static: worker ingestion cycle is not running — check Railway logs
- [ ] **Confirm conflicts exist**
  - `https://crisislens-api.onrender.com/api/v1/conflicts?limit=5` → non-empty array
- [ ] **Confirm Groq usage is within daily cap**
  - Railway worker logs → search for `[groq] Daily cap reached`
  - If firing daily: tighten task13/14 batch sizes or switch to 8B model for summaries
- [ ] **Confirm Supabase storage is below 400MB**
  - Supabase dashboard → Project → Settings → Database → Database size
  - If approaching 400MB: set `ARTICLE_RETENTION_DAYS=30` in Railway Variables tab
  - If at or above 400MB before launch: tighten retention immediately, vacuum the DB
- [ ] **Confirm Railway trial credit remaining**
  - Railway dashboard → Billing → check remaining balance
  - Trial expiry: ~2026-07-04. Upgrade to Hobby ($5/month) before credit hits $0
- [ ] **Set up uptime monitoring (UptimeRobot — free)**
  - uptimerobot.com → free account → Add Monitor × 3:
    - API health: `https://crisislens-api.onrender.com/health` — check every 5 min
    - Frontend: `https://crisis-lens-six.vercel.app` — check every 5 min
    - Railway worker: `https://web-production-f03a4.up.railway.app/` — check every 5 min
  - Set alert contact to your email
  - Railway worker: Settings → enable public domain if not already on

---

## Section 4 — Security

**Status: ✅ Done (pending domain confirmation)**

- [ ] `ALLOWED_ORIGINS` set to production domain (blocked on Section 1)
- [x] CORS `allow_methods` restricted to `GET`
- [x] `allow_credentials` set to `False`
- [x] `/health` and `/` exempt from rate limiting
- [x] Conflicts endpoints rate-limited to `20/minute, 100/hour`
- [x] No secrets committed to git
- [x] No plaintext credentials in CLAUDE.md

---

## Section 5 — Frontend Checklist

**Status: 🟡 In Progress**

- [ ] **Test on mobile** (iOS Safari, Android Chrome)
  - Cold-start overlay appears and is readable
  - Cards render correctly at narrow widths
  - Methodology panel opens and closes correctly
  - Arabic mode renders correct RTL layout
- [ ] **Test dark mode**
  - Cold-start overlay background matches theme (uses `--bg-rgb` CSS variable)
  - All cards, modals, and panels readable
- [ ] **Test language toggle**
  - All UI strings switch to Arabic
  - "Load more" button shows Arabic text
  - Retry button on error shows Arabic text
- [ ] **Test with no data** (API returns empty array)
  - Empty state message visible, not a blank page
- [ ] **Test share/hash URLs**
  - Open a conflict card → check URL bar for `#c{id}`
  - Paste that URL into a new tab → correct card opens
- [ ] **Verify load-more works**
  - If fewer than 50 conflicts exist: "Load more" button should not appear
  - If more than 50: button appears with correct remaining count
- [ ] **Verify methodology panel**
  - Opens from masthead "Methodology" button
  - All anchor links jump to correct sections
  - Closes on ✕, backdrop click, and Escape key

---

## Section 6 — Soft Launch Checklist

**Status: 🔴 Blocked on Sections 1–5**

Do these in order on launch day:

- [ ] All Section 1–5 items checked
- [ ] Take a screenshot of the live site as a record of launch state
- [ ] Share URL with 2–3 trusted people first (journalists or researchers) for a sanity check before wider distribution
- [ ] Post to Twitter/X — use the OG image link preview to verify it renders before posting publicly
- [ ] Post to relevant Reddit communities (r/geopolitics, r/journalism, r/MediaAnalysis)
- [ ] Submit to ProductHunt (schedule for a Tuesday–Thursday morning for best visibility)
- [ ] Add to GitHub README: live URL, what it does, how to run locally

---

## Open Items (Not Blocking Launch)

These are known gaps that can be addressed post-launch:

| Item | Priority | Notes |
|---|---|---|
| Per-conflict permalink pages | High | Hash URLs (`#c123`) exist but not indexable by Google. Post-launch priority. |
| Twitter/social account | High | Needed for `twitter:site` tag and inbound links from journalists |
| Google Analytics / Plausible | Medium | Zero-cost option: Plausible has a 30-day free trial, then $9/month. Alternative: Vercel Analytics (free tier available) |
| DB size monitoring in `/health` | Low | Add `db_size_mb` to health response so size is visible without logging into Supabase |
| `HF_TOKEN` cleanup in render.yaml | Trivial | No longer used by task9 — remove when confirmed no other task references it |
| SEO for Arabic content | Medium | `hreflang` tags set but no Arabic-language URL exists. When permalink pages are built, create `?lang=ar` variants |

---

## Quick Reference — Key URLs

| Service | URL |
|---|---|
| Frontend (current) | https://crisis-lens-six.vercel.app |
| API health | https://crisislens-api.onrender.com/health |
| Worker health | https://crisislens-worker.onrender.com |
| GitHub | https://github.com/daniehben/CrisisLens |
| Supabase | https://supabase.com/dashboard |
| Render | https://dashboard.render.com |
| Vercel | https://vercel.com/dashboard |
| Google Search Console | https://search.google.com/search-console (add after domain set) |

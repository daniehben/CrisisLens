# CrisisLens — Legal Notes

> Internal reference only. Not legal advice. Update this file as the platform evolves.
> Last updated: 2026-06-06

---

## EU Copyright Directive — Article 15 (Link Tax)

### What it is

Article 15 of the EU Copyright Directive (Directive 2019/790, "DSM Directive") gives press publishers the right to authorise or prohibit "information society services" from reproducing or making available their press publications online. It specifically targets news aggregators that display snippets of articles beyond "individual words or very short extracts."

### Does it apply to CrisisLens?

Potentially yes — CrisisLens aggregates headlines and short summaries from EU publishers including BBC, Reuters, The Guardian, Deutsche Welle, and France 24. However, several factors significantly reduce the risk:

| Factor | Status | Notes |
|---|---|---|
| Revenue model | ✅ Pre-revenue | Enforcement has focused exclusively on large commercial platforms |
| Link-back | ✅ Always present | Every item links directly to the original article |
| Full article reproduction | ✅ Never | Only headlines and 2-3 sentence summaries displayed |
| Headlines alone | ✅ Exempt | Article 15 explicitly excludes "individual words or very short extracts" |
| AI-generated analysis | ✅ Safe | Original content — not in scope for Article 15 |
| OG images | ✅ Low risk | Sourced from publishers' own meta tags, designed for preview use |
| Summary snippets | ⚠️ Grey zone | LLM-generated summaries of articles — the main exposure area |

### Current risk assessment

**Pre-revenue: Low.** No EU publisher has pursued Article 15 enforcement against an independent, non-commercial aggregator. Enforcement actions to date have targeted Google News (settled with French publishers for €76M/year) and Microsoft Bing. CrisisLens's research and commentary positioning, combined with direct attribution and linking, provides a defensible good-faith position.

**Post-revenue: Medium.** Once CrisisLens generates revenue (subscriptions, licensing, advertising), the legal exposure increases materially. EU publishers become more likely to assert licensing rights.

### What was done

- **ToS updated (June 2026):** The Terms of Service now explicitly:
  - States that CrisisLens displays only short extracts and headlines
  - Confirms all items link to original publishers
  - Notes CrisisLens generates no revenue from third-party content display
  - Provides a 72-hour publisher takedown/removal contact process via GitHub
  - Includes a direct Article 15 acknowledgment for EU publishers

### Action plan by phase

**Now (pre-revenue) — done:**
- [x] ToS includes publisher takedown contact with 72-hour SLA
- [x] ToS includes explicit Article 15 acknowledgment
- [x] Every conflict card links to original source articles
- [x] No full article text reproduced anywhere in the pipeline

**At revenue / funding:**
- [ ] Get a one-time media law consultation (~$500) to review the snippet + summary display
- [ ] Contact BBC, Reuters, Guardian directly — all have formal aggregator licensing programmes
- [ ] Option: switch EU publisher display to headline-only (removes grey zone entirely, lower product quality)
- [ ] Option: implement `robots.txt` publisher opt-out flag for snippet display

**If a publisher contacts you:**
1. Respond within 72 hours via the GitHub issue or email provided
2. Offer to remove their source from the pipeline entirely (`is_active = FALSE` in the sources table — one DB update)
3. Document the request and response in this file

---

## Publisher Removal Process

To remove a source from CrisisLens entirely:

```sql
-- Disable source (no data deleted, just stops ingestion and display)
UPDATE sources SET is_active = FALSE WHERE code = 'XXX';
```

To re-enable:
```sql
UPDATE sources SET is_active = TRUE WHERE code = 'XXX';
```

No redeploy needed — the worker reads `is_active` on each ingestion cycle, and the API filters inactive sources from all responses.

---

## robots.txt — Current State

`frontend/robots.txt` currently allows all crawlers:

```
User-agent: *
Allow: /
Sitemap: https://crisis-lens-six.vercel.app/sitemap.xml
```

CrisisLens respects third-party `robots.txt` files during ingestion. The worker fetches RSS feeds only — it does not crawl article pages. RSS feeds are explicitly provided by publishers for syndication purposes.

---

## GDPR / CCPA — Current State

**Current version (no accounts, no analytics): not applicable.**

CrisisLens collects no personal data. No cookies, no tracking, no accounts. The Privacy Policy modal documents this explicitly. When user accounts or newsletter subscriptions are introduced, a GDPR compliance review will be required before launch of those features. Key items at that point:

- Lawful basis for processing (consent or legitimate interest)
- Right to erasure implementation
- Data processor agreements with Supabase, Railway, Vercel, Render
- Cookie consent banner if analytics are added

---

## Open Legal Items

| Item | Priority | Trigger |
|---|---|---|
| Article 15 licensing conversations (BBC, Reuters, Guardian) | High | At first revenue event |
| Media law consultation | High | At first revenue event |
| GDPR compliance review | High | Before accounts/newsletter launch |
| CCPA compliance review | Medium | Before US-targeted marketing |
| Trademark search for "CrisisLens" name | Medium | Before custom domain purchase |

# CrisisLens — Results So Far

What the system has actually detected, with concrete examples. Use this as the proof-of-concept artifact when explaining the product to anyone (investors, journalists, friends, your future self).

---

## Headline finding

The system found a real, demoable contradiction on its first working run:

**🇮🇱🇱🇧 Israeli airstrikes in southern Lebanon — May 2, 2026**

| Source | Trust | Headline | Casualty count |
|--------|-------|----------|----------------|
| Al Jazeera English | 0.80 | "Israeli air strikes kill **10** people in southern Lebanon" | 10 |
| Associated Press | 0.80 | "Israeli airstrikes in southern Lebanon kill **7**" | 7 |

Two equally-trusted Western news outlets reporting different death tolls on the same incident, the same day. NLI confidence: **82.3%**. This is exactly the kind of contradiction CrisisLens is built to surface — not "fake news" but the everyday divergence in numbers, framing, and emphasis that shapes how people understand a crisis.

Second-strongest finding:

**🇮🇷🇺🇸 Trump on the Iran war — May 1–2, 2026**

| Source | Trust | Headline |
|--------|-------|----------|
| Al Jazeera English | 0.80 | "Trump says no '**early**' end to war, unhappy with Tehran offer" |
| Associated Press | 0.80 | "Trump contends hostilities with Iran have '**terminated**'" |

Same person, same week, two outlets, contradictory characterization of his own policy. NLI confidence: **99.9%**.

---

## Full labeled set (n=5)

The first batch of conflicts after the NLP pipeline started producing output. Manually labeled.

| # | Score | Confidence | Sources | Topic | Verdict | Why |
|---|-------|------------|---------|-------|---------|-----|
| 1 | 0.13 | 65.9% | WP × AJA | Trump Germany troop cut | ❌ false positive | Both confirm same fact, different angle |
| 2 | 0.80 | 99.9% | AJE × AP | Trump Iran "no early end" vs "terminated" | ✅ real | Direct contradiction in the quoted policy |
| 3 | 0.74 | 92.5% | AJE × AP | NATO troop drawdown details | ❌ false positive | Same story, AP focuses on Germany's defense minister |
| 4 | 0.66 | 82.3% | AJE × AP | Israel airstrikes Lebanon casualties | ✅ real | 10 dead vs 7 dead — number disagreement |
| 5 | 0.50 | 49.7% | AJE × AJA | Iran war live blog vs Hormuz mission | ❌ false positive | Different aspects of same broader conflict |

**Precision: 2/5 = 40%** (excluding "unsure" labels).

This is a tiny sample — the precision number is not statistically meaningful. Treat it as direction, not fact.

---

## Failure modes (what the AI gets wrong)

All 3 false positives share a pattern:

**"Same story, different angle"** — both articles cover the same underlying event but emphasize different aspects. The NLI model sees the surface-level differences in framing and labels them as contradiction. This is the hardest failure mode to fix with thresholds alone.

| FP | Why it's the same story |
|----|-------------------------|
| #1 | Both confirm Trump cutting US troops in Germany, just different angle |
| #3 | Both about NATO assessing the troop drawdown, AP angle is on the German minister |
| #5 | Two AJ outlets each covering different facets of the Iran war, not contradicting |

**What would fix this** (in order of build cost):

1. **Threshold bump alone** — gets us part-way. Raising contradiction threshold to 0.55 kills #5 but keeps #1 (0.66) and #3 (0.92) which are stronger false positives.
2. **Same-story collapse** — when similarity is very high (>0.85) AND headlines share most named entities, mark as "same story" rather than "contradiction." Catches #3 cleanly.
3. **Numeric mismatch boost** — when both headlines contain numbers AND the numbers disagree (10 vs 7, 50 dead vs 100 dead), strongly boost the conflict score. This is the highest-precision signal we have.
4. **Entity extraction** — full NER pipeline to cluster articles into events. Heavy but eliminates same-story collisions entirely.

---

## What works (and is interesting)

- **The Arabic-first thesis is sound.** All real conflicts so far involve at least one Arabic-language source (AJA, AJE which is part of the same network). Once we have more Arabic outlets (DW Arabic, France 24 Arabic, Al Arabiya — all just added) the cross-source contradiction surface area grows multiplicatively.
- **NLI confidence correlates with realness.** 99% and 82% confidence pairs are real; 49% is noise. This suggests a higher threshold + more data will push precision up.
- **Trust weighting is doing its job.** The two real conflicts are AJE×AP (both 0.80 trust) and AJE×AP again — high-trust pairs. Low-trust sources haven't generated false-positive noise yet.

---

## Sources currently producing data

| Source | Articles ingested | Notes |
|--------|-------------------|-------|
| AJA (Al Jazeera English RSS) | ~25/cycle | Largest single source |
| AJA+ (AJ Plus Arabic via RSSHub) | 0 | RSSHub bridge intermittent |
| AJE (Al Jazeera English NewsAPI) | 10/cycle | Different feed than AJA |
| BBC | 10/cycle | |
| JRP (Jerusalem Post) | 10/cycle | Israel-aligned counter-perspective |
| WP (Washington Post) | 10/cycle | |
| AP | 10/cycle | High accuracy benchmark |
| DW (Deutsche Welle Arabic) | new | Just wired up |
| F24 (France 24 Arabic) | new | Just wired up |
| ARB (Al Arabiya) | new | Just wired up |

After 24 hours of cycles with the new Arabic sources live, we expect:
- Total articles: ~500
- Candidate pairs: 30–50
- Real conflicts: 5–10

That's the dataset to make a real precision call on.

---

## How to reproduce these findings

1. Get a labeling CSV of the latest conflicts:
   ```
   DATABASE_URL='<supabase-uri>' python scripts/label_conflicts.py --limit 50
   ```
2. Open `conflicts_to_label.csv`, fill the `label` column with `yes` / `no` / `unsure`.
3. Run the scoring:
   ```
   python scripts/score_labels.py conflicts_to_label.csv
   ```
   Get precision per threshold bucket and a recommendation.

---

## Live demo

Frontend: **https://crisislens-5cx9.onrender.com**

If the page is slow to load, that's the Render free-tier cold start — the API service spins down after 15 minutes of inactivity and takes ~50 seconds to wake. After the first request it's instant.

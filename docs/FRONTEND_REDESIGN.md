# CrisisLens — Frontend Redesign Brief
_Generated: 2026-05-14_

---

## The Core Problem

The current site looks like a **data dashboard**, not a news product.

Every card bleeds region colors — terracotta, teal, lavender, gold, muted red — simultaneously. The source pills have colored backgrounds. The trust bars add more color. The spectrum bar adds even more. A visitor scanning the page sees a design tool, not a news interface.

The design language contradicts the editorial gravity of the content. You're surfacing contradictions in war coverage. That deserves the visual weight of the Financial Times, not a startup SaaS product.

**Goal of the redesign:** shift from "data dashboard" → "editorial authority."

---

## Reference Sites & What to Take From Each

| Site | What to steal |
|------|--------------|
| **Reuters.com** | Near-monochrome palette, top category nav, clean card grid, typography hierarchy |
| **AP News** | Maximum whitespace, ample breathing room, minimal color — just one red accent |
| **Financial Times** | Serif headlines on every story card, strong typographic contrast as the primary visual language |
| **Al Jazeera English** | Horizontal section nav with regions/topics, dark nav bar authority |
| **The Guardian** | Thin colored section labels (just a colored line/word, NOT a colored pill) |
| **NYT** | Two-column card grid, featured row at top, story hierarchy by size |

---

## Design Changes — By Area

### 1. Color Palette (most impactful change)

**Current:** 9 region colors showing simultaneously, terracotta accent, colored source pills with background fills.

**New:** Near-monochrome. One red accent for contradiction signals only.

```
--bg:        #0f0f10    /* near-black — darker than current */
--card:      #161618    /* card surface */
--line:      #252528    /* borders */
--ink:       #e8e4dc    /* body text */
--muted:     #6b6760    /* metadata */
--accent:    #c0392b    /* ONE accent — deep red for contradiction */
--accent-soft: rgba(192,57,43,.10)
```

**Remove entirely:**
- All per-region colors on card surfaces (--west, --mena, --gulf, etc.)
- Colored pill backgrounds on source labels
- Trust bars (the colored horizontal bar under source name)
- Spectrum bar on card face

**Keep:**
- Region colors exist in CSS but are used ONLY as a 6px dot beside the source name in the modal detail view
- That's it. One small dot. Not a background fill.

---

### 2. Typography

**Add:** A serif display font for contradiction headlines. This single change makes everything feel more authoritative.

```html
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800&family=Inter:wght@400;500;600&family=Tajawal:wght@400;500;700&display=swap" rel="stylesheet">
```

- **Headlines on cards:** Playfair Display 700 (or system serif as fallback)
- **UI elements, metadata, buttons:** Inter 400/500
- **Arabic text:** Tajawal (unchanged)

The serif creates instant editorial authority. FT does this. NYT does this. Every serious news outlet does this.

---

### 3. Navigation — Category Tabs

**Remove:** The flat chip filters (Lang / Sort / Source) scattered across two rows.

**Add:** A sticky horizontal category tab bar below the header. This is the single biggest UX improvement.

```
[ All ] [ Palestine ] [ Iran ] [ Lebanon ] [ Syria ] [ Yemen ] [ Ukraine ] [ Finance ]
```

Implementation: **client-side keyword matching** on headlines. No DB changes needed. Each topic maps to a keyword list that runs against `headline_1_en + headline_2_en`.

```javascript
const TOPICS = {
  palestine: ['palestine','gaza','west bank','hamas','rafah','idf','settler'],
  iran:      ['iran','tehran','khamenei','irgc','nuclear','sanctions'],
  lebanon:   ['lebanon','beirut','hezbollah','nasrallah','south lebanon'],
  syria:     ['syria','damascus','aleppo','idlib','hts'],
  yemen:     ['yemen','houthi','sanaa','hodeidah'],
  ukraine:   ['ukraine','kyiv','russia','zelensky','nato'],
  finance:   ['oil','dollar','gdp','inflation','sanctions','economy','barrel'],
};
```

Keep Lang (All / Arabic / English) and Sort (Strongest / Recent) as small chips — just move them to the right side of the category bar or into a compact "Filter" dropdown.

---

### 4. Layout Structure

**Current:** Single-column endless scroll.

**New:** Three-zone page layout.

```
┌─────────────────────────────────────────────────────┐
│  Header: Logo + Lang + Theme + GitHub                │
│  Category nav: [All][Palestine][Iran][Lebanon]...    │
├─────────────────────────────────────────────────────┤
│  FEATURED ZONE — "Most Controversial Today"          │
│  2 large cards side-by-side (top weighted_score)     │
│  Clearly labeled: "MOST DISPUTED · LAST 24H"         │
├─────────────────────────────────────────────────────┤
│  FEED — 2-column card grid                           │
│  Left col: by controversy score                      │
│  ↓ ordered by weighted_score / recent / etc.         │
└─────────────────────────────────────────────────────┘
```

On mobile: single column throughout. Featured cards stack vertically.

---

### 5. Card Redesign

**Current card anatomy:** colored region pills + colored background side blocks + trust bars + spectrum bar + badge row + source name + headline pair.

**New card anatomy — much leaner:**

```
┌──────────────────────────────────────────────────────┐
│  BBC  ·  Al Jazeera English                 2h ago ↗ │  ← metadata row, no color
│                                                       │
│  "Israeli forces deny targeting civilian             │  ← headline 1, SERIF font
│   shelter in northern Gaza"                          │
│  ——————————————————————                               │  ← faint divider line
│  "IDF confirms strike on Hamas command               │  ← headline 2, SERIF font
│   post in Jabalia refugee camp"                      │
│                                                       │
│  [≠ numeric]  [↔ cross-perspective]     ▓▓▓░░ 78%  │  ← badges + score bar
└──────────────────────────────────────────────────────┘
```

What changed on the card:
- No colored side panels / background fills
- Headlines are now in **Playfair Display** (serif) — looks editorial, not data
- Source names are plain text, no pills
- Tiny colored dot beside each source name (region color, very subtle)
- Confidence bar moved to bottom-right, small
- Card hover: single subtle left-border appears in the accent red

---

### 6. Remove the Source Chips Row

The 20+ source filter buttons (AJA, BBC, AP, WP, JRP...) are overwhelming and meaningless to casual users. Remove from the main page.

**Replace with:** A "Sources" link in the nav that goes to a dedicated sources page (or modal) listing all outlets with their trust scores and region categorization. This cleans the main feed enormously.

---

### 7. Featured "Most Disputed" Section

Pin the top 2 stories by `weighted_score` from the last 24 hours in a visually distinct section at the top, with a clear label. This answers the question "what's the most controversial right now?" without requiring the user to know what "Strongest" sort means.

Visual treatment: slightly larger cards, a thin red left-border, the label "MOST DISPUTED · NOW" in small caps above.

---

### 8. Stats Bar — Simplify

Current stats row shows: `X contradictions · Y shown · Z cross-perspective · [Refresh]`

Keep it, but move to right side of the category nav. Make it feel like a wire counter:
```
↻  47 contradictions live · 12 cross-perspective
```

---

## What NOT to Change

- Dark/light theme toggle — keep it
- Arabic/English language toggle — keep it
- Modal detail view — it's good. Just kill the colored backgrounds in it.
- Methodology section — keep it, it builds trust
- The underlying data model and API — zero changes needed

---

## Implementation Order (if building from scratch)

1. **Palette + Typography** — CSS-only change, zero JS. Biggest visual impact, 30 min.
2. **Category tabs** — Add keyword-matching JS and horizontal nav. 2 hours.
3. **Card redesign** — Rework the `card()` function. 2-3 hours.
4. **Featured section** — Extract top 2 stories and render them separately. 1 hour.
5. **Remove source chips** — Delete 10 lines. 5 min.
6. **Two-column grid** — Change `grid gap-5` to `grid grid-cols-1 md:grid-cols-2 gap-5`. 2 min.

Total: ~1 day of focused frontend work.

---

## Prototype

See `frontend/redesign-prototype.html` — a fully working static prototype demonstrating the new palette, typography, category tabs, featured section, and card redesign. Uses real API data.

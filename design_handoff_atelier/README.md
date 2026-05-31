# Handoff: Job·Apply — "Atelier" result panel

## Overview
Redesign of the **Job·Apply** Chromium extension — a tool that scrapes a job posting from
the current page, enriches/filters it with an LLM, and lets the user auto-apply. This bundle
covers the **job-result screen** (the panel shown after a posting is analysed) in the chosen
direction, **"Atelier"**: a clean, productivity-tool aesthetic (Raycast / Notion / Linear
family) — neutral light surface, monospaced micro-labels, one calm green accent, keyboard-first.

## About the design files
The file in this bundle (`atelier-reference.html`) is a **design reference created in HTML** —
a static prototype showing the intended look, spacing, and copy. It is **not production code to
copy verbatim**. The task is to **recreate this design inside the extension's existing
codebase**, using its established framework and patterns (React/Preact/vanilla + whatever CSS
approach is already in use). If the extension UI has no framework yet, plain HTML/CSS or a light
React setup is appropriate — match what's already there.

## Fidelity
**High-fidelity.** Colors, typography, spacing, radii, and copy are final. Recreate pixel-for-pixel,
then wire to real scraped data. The only deliberately faked values are the scraped content itself
(job title, company, metadata, description) and the `92% match` score — those come from the
scraper + LLM at runtime.

---

## Screen: Result panel

**Purpose:** After the user clicks "Analyser la page", the extension scrapes the posting, runs
the LLM enrichment/filtering, and shows this panel. The user reviews the structured offer and its
match score, then triggers **auto-apply** (the product's core action) or opens the original posting.

**Container:** Designed as a **400px-wide** extension surface (popup or side panel). Height is
content-driven; the reference frames it at 860px with a rounded 20px card + soft shadow for
presentation — in the real extension the panel fills the popup/side-panel viewport (drop the outer
radius/shadow if it's edge-to-edge; keep the **internal** card radii).

**Layout (top → bottom), single column flexbox:**

1. **Top bar** — sticky header, white, 1px bottom hairline. `padding:14px 18px`.
   - Left: brand lockup — an 18×18 dark rounded-square mark with a white "J", then "Job Apply" (13px / 600).
   - Right: a keyboard-hint chip "⌘ ↵ pour postuler" (mono, 10.5px, muted, bordered pill).
2. **Body** — `padding:22px 22px 0`, scrolls if needed.
   - **AI tag** — pill "✦ filtré par LLM · 92% match" (mono 10px, green text on green tint). The `92%`
     is the LLM compatibility score; show it here.
   - **Company row** — 26×26 rounded "logo" square with the company initial, then "BNP Paribas · Singapour" (13px muted).
   - **Title** `<h1>` — job title, 22px / 700 / -0.02em, line-height 1.22.
   - **Metadata table** — bordered, 13px-radius rounded container, white, rows divided by hairlines.
     Each row: mono lowercase label (left) + value (right, 13.5px / 600). Empty/unknown values use
     muted text and a phrase like "non précisé" rather than a bare dash.
     Rows: `contrat`, `salaire`, `télétravail`, `expérience`, `publié` (add `expire` when present).
   - **Description** — 13px, line-height 1.6, **clamped to 4 lines** (`-webkit-line-clamp:4`) with overflow hidden.
3. **Footer** — sticky bottom, white, 1px top hairline, `padding:14px 18px 18px`, flex row, gap 10px.
   - **Primary CTA** (flex:1, 46px tall, dark `--ink` fill, white text, 11px radius): "Postuler" + a
     small mono keycap "⌘↵". This is the **auto-apply** trigger.
   - **Secondary icon button** (46×46, bordered, light): "↗" — opens the original posting in a new tab.

## Interactions & behavior
- **Primary CTA / `⌘↵`** → kick off the auto-apply flow. Show a loading state on the button
  (spinner or "Envoi…"), disable while in flight, then a success/error state.
- **↗ icon** → `window.open(offer.sourceUrl, '_blank')`.
- **Description** "clamp" is purely visual; if you add a "Lire la suite" affordance, toggle the
  `-webkit-line-clamp` off on click.
- **Hover:** CTA dips opacity to .9 and translates 1px down on `:active`; icon button background
  darkens slightly. Transitions ~120–150ms.
- **Loading state (pre-result):** the scrape + LLM step takes time — show a lightweight progress
  state before this panel (the old flow called it "Étape 02 / 02 · Extraction + filtrage LLM").
  Reuse the same tokens: mono label, hairline, green accent.

## State management
- `status`: `'idle' | 'scraping' | 'enriching' | 'ready' | 'applying' | 'applied' | 'error'`.
- `offer`: `{ title, company, location, contract, salary, remote, experience, publishedAt, expiresAt, description, sourceUrl }` — from scraper.
- `match`: `{ score: number /* 0–100 */, ... }` — from LLM enrichment. Drives the AI tag's "% match".
- Auto-apply submits and transitions `applying → applied | error`.

---

## Design tokens

| Token | Value | Use |
|---|---|---|
| `--bg` | `#f7f7f5` | Panel/app background |
| `--pan` | `#ffffff` | Bars, cards, metadata table |
| `--ink` | `#23241f` | Primary text + primary button fill |
| `--mut` | `#82837b` | Secondary text, mono labels |
| `--faint` | `#b4b5ac` | Tertiary / empty values |
| `--line` | `#e9e9e3` | Hairline borders & dividers |
| `--ac` | `#3d7d5a` | Accent green (AI tag, match) |
| `--ac-soft` | `#e7f1eb` | Accent tint background |
| Panel outer border | `#ececE6` (≈ `#ECECE6`) | Outer card edge |

**Typography**
- Sans (UI): **Hanken Grotesk**, weights 400/500/600/700. Fallback `system-ui, sans-serif`.
- Mono (labels, keycaps, AI tag): **Spline Sans Mono**, weights 400/500. Fallback `ui-monospace, monospace`.
- Base 14px / line-height 1.5. Title 22px/700/-0.02em. Mono labels 10–11px, letter-spacing .02–.04em, lowercase.

**Spacing** — 4px base. Common: bar `14×18`, body `22`, row `11×15`, footer `14×18×18`, gaps 8–10px, block margins 16–18px.

**Radii** — panel 20, cards/table 13, buttons/tag 11/7, mark/logo 6–7, keycap 5.

**Shadow (presentation only)** — `0 18px 50px -20px rgba(40,40,30,.28)`.

**Icons/glyphs** — text glyphs only: `✦` (AI), `↗` (external link), `⌘ ↵` (keycaps). Swap for your
icon set if you have one; keep them small and monochrome (`--mut`).

## Assets
None. No raster images. Company "logo" is a letter tile (first initial on `#eceae2`); replace with a
real favicon/logo fetch if available. Fonts load from Google Fonts in the reference — vendor them
locally (woff2) for a packaged extension so it works offline and avoids remote requests.

## Files
- `atelier-reference.html` — standalone, self-contained live reference of this screen. Open in a
  browser to inspect exact rendering; all CSS is inline with token comments.

## Notes for the implementer
- Keep it **airy and edge-free**: hairline borders over heavy shadows, generous padding, no harsh contrast.
- The green is the *only* color — everything else is neutral. Don't introduce extra hues.
- Mono is reserved for *labels and keycaps*, never for content values or the title.
- French copy throughout (current product language); keep it unless localizing.

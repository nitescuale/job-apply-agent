# Job Apply Agent

Chrome / Firefox extension that analyses a job posting, scores it against
your profile, tailors a fresh ATS-friendly CV (PDF) from your DOCX, fills
the application form, and tracks every offer in a local SQLite store with
a full-page dashboard.

> Click on a job posting → it scrapes, filters, scores, generates a
> tailored CV, optionally fills the form, and records the application.
> You review, you submit, you track everything afterwards.

The product UI is in French, but the codebase, docs and commit history are
in English.

---

## Stack

- **Extension** — React 18 + TypeScript, Vite 5 + `@crxjs/vite-plugin` (Manifest V3, Chrome + Firefox)
- **Backend** — Python 3.11+, FastAPI, uvicorn, [Scrapling](https://github.com/D4Vinci/Scrapling) (HTML parser)
- **Local DB** — `sqlite3` stdlib (no SQLAlchemy/Alembic, schema via `CREATE TABLE IF NOT EXISTS`, idempotent init via FastAPI `lifespan`)
- **LLM** — Google Gemini (`google-genai`), `gemini-2.5-flash`, free tier
- **CV pipeline** — `python-docx` edits the source DOCX in place (text of the runs is replaced, formatting/styles preserved, scope strictly limited to SUMMARY + "Relevant coursework"), then `docx2pdf` (Word COM) converts to PDF — LibreOffice headless as a fallback
- **Design** — "Atelier" light, Hanken Grotesk + Spline Sans Mono, single green accent (`#3d7d5a`)

## Architecture

```
job-apply-agent/
├── extension/                       Chrome / Firefox Extension (Vite + CRXJS)
│   └── src/
│       ├── background.ts            Service worker: runs scrape / match /
│       │                            tailor / fill pipelines, writes to
│       │                            chrome.storage.local, PATCHes status.
│       ├── content/scraper.ts       Captures HTML, detects + fills the form
│       ├── shared/status.ts         Source of truth for application statuses
│       ├── popup/Popup.tsx          7 states: idle → scraping → ready →
│       │                            applying → applied | error | apply-error.
│       │                            Status dropdown + Suivi (tracker) button.
│       └── tracker/                 Full-page tracker (opens in a tab):
│           ├── index.html           list grouped by company, filter chips,
│           ├── index.tsx            search, inline status + notes editing,
│           └── Tracker.tsx          per-row links to original offer + CV.
├── backend/
│   ├── main.py                      FastAPI: /health, /scrape-job,
│   │                                /fill-form, /tailor-cv, /match-score,
│   │                                /open-file, /applications*
│   ├── store.py                     SQLite layer (applications + scrapes
│   │                                cache, hash-based dedup, PATCH helpers)
│   ├── agents/
│   │   ├── job_scraper.py           Scrapling: JSON-LD → meta → text fallback
│   │   ├── llm_extractor.py         Gemini: filters noise, structures essentials
│   │   ├── form_filler.py           Gemini: maps form_schema + profile → values
│   │   ├── match_scorer.py          Gemini + deterministic overlap fallback:
│   │   │                            score 0-100, matched / missing skills
│   │   └── cv_tailor.py             python-docx in-place edits + docx2pdf → PDF
│   └── data/
│       ├── user_profile.example.json
│       ├── user_profile.json        (gitignored, your real profile)
│       └── applications.db          (gitignored, SQLite tracking + cache)
├── scripts/
│   └── build-firefox.mjs            Firefox MV3 build (gecko id, classic
│                                    background script, web-ext lint clean)
├── tests/                           pytest — 129 green tests
├── dev.ps1                          Boots backend + Vite in parallel (Windows)
└── design_handoff_atelier/          "Atelier" design reference
```

## Pipelines

### 1. Offer analysis + dedup + cache — `POST /scrape-job`

```
Rendered HTML (from content script)
        ↓
Scrapling: JSON-LD JobPosting (@graph-aware) → Open Graph meta tags
           → site-specific selectors → <title>/<body> fallback
        ↓
compute_job_hash(title, company, location)   NFKD + ASCII + lowercase
        ↓
SQLite cache hit? → return cached essentials, skip Gemini (from_cache: true)
SQLite cache miss → Gemini LLM filters noise, structures title, company,
        location, contract_type, salary, remote, experience_level,
        skills[], missions[], summary
        ↓
upsert applications row (status='seen' on first sight, preserved otherwise)
+ save scrapes cache
        ↓
Returns the essentials + {llm_used, from_cache, application_id,
        seen_before, application_status} → popup renders + shows badge
```

If `GEMINI_API_KEY` is missing or the API fails, the LLM step is gracefully
skipped (`llm_used: false`). Re-scraping an offer you already marked
`applied` does **not** revert it to `seen` (status is preserved on UPDATE
via `COALESCE`).

### 2. Match score — `POST /match-score`

Triggered automatically by the service worker after `/scrape-job`. The
score lands on `result.match` in `chrome.storage.local` and the popup
renders it inline.

```
offer + profile (skills, summary, experience, education)
        ↓
GEMINI_API_KEY set?  → Gemini returns a strict JSON
                       {score 0-100, matched_skills, missing_skills,
                        rationale ≤ 280 chars}. Markdown fences tolerated.
no key / Gemini KO   → Deterministic overlap fallback (normalised NFKD +
                       lowercase, dedup): matched / total_offer_skills * 100.
                       Overlap (not Jaccard) so extras don't penalise.
        ↓
Persist match_score on the matching applications row if it exists
(/match-score never CREATES rows — that's /scrape-job's job).
        ↓
Returns {score, matched_skills, missing_skills, rationale, llm_used,
         application_id?}
```

The popup `MatchCard` shows a big colour-coded number (green ≥ 70, amber
45-69, red < 45), a progress bar, the rationale, and chips of the missing
skills. The agent **never raises to the caller** — failure logs and falls
back, so the gauge always renders.

### 3. CV tailoring — `POST /tailor-cv`

The core idea: don't reinvent the layout. Take the user's existing DOCX,
rewrite **only** the SUMMARY content and the "Relevant coursework" line in
EDUCATION, and convert the modified DOCX to PDF via Word so the visual
result is 1:1 with the source.

```
profile.base_cv_path (DOCX) → python-docx loads the document
        ↓
_collect_paragraphs walks top-level paragraphs + table cells in
        reading order (each paragraph gets a sequential index).
        ↓
_collect_editable_in_sections — section-aware filter:
  • walks paragraphs linearly tracking the current section header
    (SUMMARY / EXPERIENCE / EDUCATION / PROJECTS / SKILLS / ...)
  • keeps substantive paragraphs under SUMMARY
  • keeps the "Relevant coursework: ..." line under EDUCATION
  • everything else (EXPERIENCE bullets, PROJECTS descriptions,
    SKILLS, LANGUAGES, CERTIFICATIONS, contact, headers) is frozen
        ↓
Gemini: receives {idx: text} for those paragraphs + the offer + the
        profile + the optional match block (from /match-score).
        Returns a strict JSON {idx: rewritten_text}. ±25 % length,
        mirrors offer keywords when truthful, never invents.
        Banned clichés (passionate, team player, fast learner, ...)
        are blocked in the prompt. If a `match` is forwarded, the
        prompt receives matched_skills_emphasize_truthfully and
        missing_skills_do_not_claim_present — keeps Gemini honest
        about gaps.
        ↓
_set_paragraph_text: for each edited index, writes the new text
        into paragraph.runs[0].text and empties the other runs.
        Formatting (font, bold, italic, colour, size, alignment) of
        the first run is preserved.
        ↓
doc.save() → tailored DOCX
        ↓
_convert_docx_to_pdf: docx2pdf (Word COM, 1:1 fidelity) → fallback
        soffice --headless if Word is unavailable.
        ↓
Saved side by side in {cv_output_dir}/{Company_Sanitized}/:
  0_CV_Firstname_Lastname_JobTitle.docx
  0_CV_Firstname_Lastname_JobTitle.pdf
```

Filename convention: `0_CV_Firstname_Lastname_JobTitle.pdf`. Title-case
per token, ALL-CAPS acronyms ≤ 4 chars preserved (AI, ML, NLP, BS, MS).
Job title is canonicalised first: drops parenthetical suffixes,
everything after a dash, and gender markers (F/H, H/F, M/F). Company is
the parent folder, not the filename.

The PDF opens through the backend (`POST /open-file` → `os.startfile` /
`open` / `xdg-open`) instead of `chrome.tabs.create({url: 'file://...'})`
which Chrome and Firefox both block by default.

### 4. Form auto-fill — `POST /fill-form`

```
Content script DETECT_FORM → schema {fields: [{id, label, type, ...}]}
        ↓
Gemini: receives form_schema + user_profile + context (title, company),
         returns {field_id: value} under strict rules (never invent,
         respect select/radio options, interpolate {title}/{company}
         into the cover letter)
        ↓
Content script FILL_FORM: React-safe native value setter, DataTransfer
         trick for <input type=file>, amber highlight on filled fields
        ↓
{filled, skipped} report shown in the popup
        ↓
Auto-PATCH: if an application_id is in scope, the service worker PATCHes
         status='applied' on the SQLite row. Status badge updates live.
```

`/fill-form` is decoupled from `/scrape-job`. The idle screen exposes two
CTAs — *Analyser l'offre* and *Remplir le formulaire* — so you can fill a
form directly on a candidate page without scraping the offer first.

## Local persistence

Two independent layers:

- **`chrome.storage.local`** — popup state (current offer, CV result,
  inflight pipeline, match score). All work runs in the **background
  service worker** so closing the popup does not abort the analysis. The
  popup hydrates + subscribes to `onChanged`. Stale `inflight > 90 s`
  coerces back to `error` (worker likely killed).

- **SQLite** `backend/data/applications.db`:
  - `applications` (id, job_url, job_hash UNIQUE, company, title, location,
    contract_type, status, match_score, cv_path, notes, created_at,
    updated_at). Statuses: `seen → applied → followed_up → interview →
    response_pos | response_neg`. Re-scraping never demotes a status.
  - `scrapes` (job_hash, essentials_json, created_at) — caches the Gemini
    extraction. Hit = no second Gemini call on the same offer.

The full-page **tracker** (`chrome-extension://<id>/src/tracker/index.html`,
opened from the popup TopBar) lists every application grouped by company,
with inline status + notes editing, status chip filters, company search,
links to the original offer and the tailored CV.

## Quick start

### Prerequisites

- Node.js 18+
- Python 3.11+ (also works on 3.10)
- A free Gemini API key: <https://aistudio.google.com/apikey> (optional —
  without it, every Gemini step degrades gracefully)

### 1. Clone and install

```powershell
git clone https://github.com/nitescuale/job-apply-agent.git
cd job-apply-agent

# Backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Extension
cd extension
npm install
cd ..
```

### 2. Configure

Create a `.env` at the repo root:

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
# optional override (defaults to backend/data/applications.db)
# DB_PATH=C:\path\to\applications.db
```

Copy the profile template and edit it:

```powershell
Copy-Item backend\data\user_profile.example.json backend\data\user_profile.json
# Edit backend\data\user_profile.json (gitignored)
```

For CV tailoring, fill in:
- `base_cv_path` — absolute path to your source CV in `.docx` format
- `cv_output_dir` — absolute path to the root folder where tailored PDFs
  will be written (subfolders are created per company)

### 3. Run

```powershell
.\dev.ps1
```

The script kills any orphan process on ports 8000/5173, then opens:
- **Backend** on <http://localhost:8000> (uvicorn --reload). SQLite is
  created on first startup via the FastAPI `lifespan` hook.
- **Vite** on <http://localhost:5173> (HMR).

### 4. Load the extension in Chrome

```powershell
cd extension
npm run build
```

1. Go to `chrome://extensions/`
2. Enable **Developer mode**
3. **Load unpacked** → select `extension/dist/`

While Vite is running, TS/TSX/CSS changes are hot-reloaded. For
`manifest.json` changes you need to reload the extension manually.

### 4-bis. Load the extension in Firefox

```powershell
cd extension
npm run build:firefox
```

Produces `extension/dist-firefox/` — a Firefox MV3 build patched from the
Chrome dist: gecko id, classic background script instead of ESM service
worker, `data_collection_permissions: ["none"]`. Validated with
`web-ext lint` (0 errors / 0 notices). Then:

1. Open `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on…** → pick any file inside `dist-firefox/`

Temporary add-ons survive until Firefox restarts.

## Usage

1. Open a job posting (LinkedIn, HelloWork, Indeed, WTTJ, JobTeaser, ...)
2. Click the extension icon → **Analyser la page**
3. The popup renders the structured offer + a match score card showing
   `score / 100`, rationale, and chips of the missing skills
4. The status of the offer is editable from the dropdown next to the
   match card (`Déjà vu`, `Déjà postulé`, `Relancée`, `Entretien`,
   `Réponse positive`, `Réponse négative`)
5. **Adapter le CV** generates a tailored PDF in
   `{cv_output_dir}/{Company}/0_CV_Firstname_Lastname_JobTitle.pdf` and
   opens it through the OS default reader
6. If the page contains an application form: **Postuler** (shortcut
   `Ctrl ↵` on Windows/Linux, `⌘ ↵` on macOS). Fields are filled
   (highlighted in amber). Successful fill auto-marks the offer as
   `applied`. You review and submit yourself.
7. **▤ Suivi** in the TopBar opens the tracker in a new tab — full
   history grouped by company, filterable by status, with editable
   notes.

## Endpoints

| Method | Route                          | Description |
|--------|--------------------------------|-------------|
| GET    | `/health`                      | `{status, llm_available, form_filler_available, cv_tailor_available, match_scorer_available}` |
| POST   | `/scrape-job`                  | Body: `{job_url, job_html}` → essentials + `{llm_used, from_cache, application_id, seen_before, application_status}` |
| POST   | `/match-score`                 | Body: `{offer}` → `{score, matched_skills, missing_skills, rationale, llm_used, application_id?}` |
| POST   | `/tailor-cv`                   | Body: `{offer, match?}` → `{saved_path, saved_docx_path, filename, folder, edited_count, editable_count}` |
| POST   | `/fill-form`                   | Body: `{form_schema, context}` → `{values, cv_base64}` |
| POST   | `/open-file`                   | Body: `{path}` → opens via OS reader. Validates path is under `cv_output_dir` and extension is `.pdf` / `.docx`. |
| GET    | `/applications`                | `?status=&company=&since=&until=` filters |
| GET    | `/applications/{id}`           | Row or 404 |
| PATCH  | `/applications/{id}`           | Body: `{status?, notes?}` → 422 on invalid status |

## Tests

```powershell
pytest -q
```

**129 tests**:
- `test_job_scraper.py` — JSON-LD, `@graph`-nested JobPosting, Open Graph meta, fallback, double-encoded HTML entities
- `test_llm_extractor.py` — mocked Gemini, malformed JSON, markdown fences
- `test_form_filler.py` — profile loading, mocked mapping, base64 CV reader
- `test_cv_tailor.py` — slug + Title-case + ALL-CAPS preservation, canonical job title, new filename convention, section detection, `_collect_editable_in_sections`, `_parse_edits`, full orchestration with mocked Gemini + mocked PDF conversion, rogue idx protection, `BANNED_CLICHES` audit
- `test_match_scorer.py` — `is_available`, clamp `[0, 100]`, normalisation, profile skills extraction (list / dict-by-category), overlap fallback (partial / full / zero / no offer skills / accents / dedup), LLM path mocked (clean JSON, fences, clamps high / low / non-numeric, truncated rationale, missing keys), cascade fallback on LLM failure / RuntimeError / non-dict response
- `test_store.py` — SQLite init idempotency, hash determinism + normalisation, upsert dedup + status preservation, COALESCE behaviour, list filters, PATCH partial + validation, cache miss / hit / upsert / unicode

## Supported sites

The extension is active on **all sites** (`<all_urls>` in the manifest).
JSON-LD scraping natively covers HelloWork, Indeed, WTTJ. LinkedIn and
JobTeaser go through site-specific selectors plus the text fallback.

## Technical notes

- **Encoding** — Windows PowerShell reads `.ps1` files as CP-1252 by default.
  `dev.ps1` is pure ASCII to avoid breakage.
- **uvicorn `--reload`** only watches `.py` files, not `.env`. If you
  change the API key, restart the backend (Ctrl+C + relaunch, or just
  re-run `dev.ps1` which kills orphans).
- **Gemini model** — `gemini-2.0-flash` was demoted from the free tier.
  Use `gemini-2.5-flash` (default) or `gemini-2.5-flash-lite` for more
  headroom.
- **React-controlled inputs** — the content script uses
  `Object.getOwnPropertyDescriptor` to call the native setter and bypass
  React/Vue intercepting `.value`.
- **CV upload** — `<input type=file>` is filled via `DataTransfer` +
  `File`. Works on most modern forms; can be blocked by strict
  validations relying on the `isTrusted` event flag.
- **CV tailoring** — input is `.docx` only (parsed via `python-docx`).
  `.doc` legacy is not supported. Slugging strips accents
  (`L'Oréal` → `LOreal`), normalises separators to `_`, Title-cases
  by token, and preserves short ALL-CAPS acronyms (BS, MS, AI, ML, NLP).
- **Opening local files** — Chrome MV3 and Firefox both block
  `chrome.tabs.create({url: 'file://...'})` by default. The extension
  routes through `POST /open-file`, validated against `cv_output_dir`
  and the `.pdf` / `.docx` allow-list to prevent arbitrary file
  execution.
- **Tracker as additional Vite entry** — CRXJS only auto-processes HTML
  files referenced by `default_popup` / `options_ui`. The tracker page
  is declared explicitly in `vite.config.ts` `rollupOptions.input` AND
  listed in `manifest.web_accessible_resources` so
  `chrome.runtime.getURL('src/tracker/index.html')` resolves.

## Status

Functional. The scrape + LLM pipeline runs on HelloWork / Indeed / WTTJ.
LinkedIn renders the structured fields but the Easy Apply form hasn't
been tested end-to-end. The form filler covers text, textarea, select,
checkbox, radio and file inputs; broader ATS coverage (Workday,
Greenhouse, Lever) is the next validation pass.

## License

Personal project, no open-source license yet.

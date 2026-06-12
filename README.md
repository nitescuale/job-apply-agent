# Job Apply Agent

Chrome extension that analyses a job posting, extracts the essentials via LLM,
auto-fills the application form from a locally stored profile, and tailors a
fresh ATS-friendly CV (PDF) to each offer from a base DOCX.

> Click on a job posting, the extension scrapes, filters, structures, generates
> a tailored CV, and offers to fill the application form. You review and you
> submit.

The product UI is in French, but the codebase, docs and commit history are
in English.

---

## Stack

- **Extension** — React 18 + TypeScript, Vite 5 + `@crxjs/vite-plugin` (Manifest V3)
- **Backend** — Python 3.11+, FastAPI, uvicorn, [Scrapling](https://github.com/D4Vinci/Scrapling) (HTML parser)
- **LLM** — Google Gemini (`google-genai`), `gemini-2.5-flash`, free tier
- **CV pipeline** — `python-docx` (DOCX input) + `markdown` + `weasyprint` (Markdown → PDF)
- **Design** — "Atelier" light, Hanken Grotesk + Spline Sans Mono, single green accent (`#3d7d5a`)

## Architecture

```
job-apply-agent/
├── extension/                       Chrome Extension (Vite + CRXJS)
│   └── src/
│       ├── content/scraper.ts       Captures HTML, detects + fills the form
│       └── popup/Popup.tsx          UI, 4 states: idle → scraping → ready → applied
├── backend/
│   ├── main.py                      FastAPI: /health, /scrape-job, /fill-form, /tailor-cv
│   ├── agents/
│   │   ├── job_scraper.py           Scrapling: JSON-LD → meta → text fallback
│   │   ├── llm_extractor.py         Gemini: filters noise, structures essentials
│   │   ├── form_filler.py           Gemini: maps form_schema + profile → values
│   │   └── cv_tailor.py             Gemini + WeasyPrint: DOCX base → tailored PDF
│   └── data/
│       ├── user_profile.example.json
│       └── user_profile.json        (gitignored, your real profile)
├── tests/                           pytest (55 green tests)
├── dev.ps1                          Boots backend + Vite in parallel (Windows)
└── design_handoff_atelier/          "Atelier" design reference
```

## Pipeline

### 1. Offer analysis — `POST /scrape-job`

```
Rendered HTML (from content script)
        ↓
Scrapling: JSON-LD JobPosting (@graph-aware) → Open Graph meta tags
           → site-specific selectors → <title>/<body> fallback
        ↓
Gemini LLM (optional): filters noise, structures title, company,
        location, contract_type, salary, remote, experience_level,
        skills[], missions[], summary
        ↓
Merged response → popup renders the result (Atelier UI)
```

If `GEMINI_API_KEY` is missing or the API fails, the LLM step is gracefully
skipped (`llm_used: false`).

### 2. Auto-fill — `POST /fill-form`

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
```

### 3. CV tailoring — `POST /tailor-cv`

```
profile.base_cv_path (DOCX) → python-docx extracts text
        ↓
(optional, profile.include_summary = true by default)
Gemini #1: writes a 2-3 sentence SUMMARY with hard rules — only facts
        from profile / base CV, 2-4 mirrored offer keywords, no banned
        clichés ("passionate", "team player", "fast learner", ...),
        no career-goal language that could disqualify for the role.
        Failure / empty output → no summary section, no crash.
        ↓
Gemini #2: receives base CV text + offer essentials + profile facts,
        returns an English Markdown CV that mirrors offer keywords
        for ATS, reorders bullets, never fabricates dates/titles,
        targets one page (~600 words). Summary section is NOT written
        by this pass — it is prepended from the first call.
        ↓
markdown → HTML → WeasyPrint → PDF (A4, Helvetica 10.5pt,
        uppercase letter-spaced sections, single column)
        ↓
Saved to {cv_output_dir}/{Company_Sanitized}/
         0_cv_firstname_lastname_jobtitle_company.pdf
```

The popup's "Adapter le CV" button triggers this and opens the resulting
PDF in a new Chrome tab via `file://`. The response includes a
`summary_used: bool` flag so callers can tell whether the SUMMARY pass
ran successfully.

## Quick start

### Prerequisites

- Node.js 18+
- Python 3.11+ (also works on 3.10)
- A free Gemini API key: <https://aistudio.google.com/apikey>

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
```

Copy the profile template and edit it:

```powershell
Copy-Item backend\data\user_profile.example.json backend\data\user_profile.json
# Edit backend\data\user_profile.json (gitignored)
```

For CV tailoring, also fill in:
- `base_cv_path` — absolute path to your source CV in `.docx` format
- `cv_output_dir` — absolute path to the root folder where tailored PDFs
  will be written (subfolders are created per company)

### 3. Run

```powershell
.\dev.ps1
```

The script kills any orphan process on ports 8000/5173, then opens two windows:
- **Backend** on <http://localhost:8000> (uvicorn --reload)
- **Vite** on <http://localhost:5173> (HMR)

### 4. Load the extension in Chrome

1. Go to `chrome://extensions/`
2. Enable **Developer mode**
3. **Load unpacked** → select `extension/dist/`

While Vite is running, TS/TSX/CSS changes are hot-reloaded. For `manifest.json`
changes you need to reload the extension manually.

## Usage

1. Open a job posting (LinkedIn, HelloWork, Indeed, WTTJ, JobTeaser, etc.)
2. Click the extension icon → **Analyser la page** button
3. The popup renders the structured offer: title, company, location, contract,
   salary, experience, dates, description
4. **Adapter le CV** generates a tailored PDF in
   `{cv_output_dir}/{Company}/0_cv_firstname_lastname_jobtitle_company.pdf`
   and opens it in a new Chrome tab
5. If the page contains an application form: **Postuler** button (shortcut
   `Ctrl ↵` on Windows/Linux, `⌘ ↵` on macOS)
6. The extension fills the fields (highlighted in amber) — you review and
   submit yourself

## Endpoints

| Method | Route          | Description                                                              |
|--------|----------------|--------------------------------------------------------------------------|
| GET    | `/health`      | `{status, llm_available, form_filler_available, cv_tailor_available}`    |
| POST   | `/scrape-job`  | Body: `{job_url, job_html}` → structured fields                          |
| POST   | `/fill-form`   | Body: `{form_schema, context}` → `{values, cv_base64}`                   |
| POST   | `/tailor-cv`   | Body: `{offer}` → `{saved_path, filename, folder, markdown, summary_used}` |

## Tests

```powershell
pytest -q
```

55 tests:
- `test_job_scraper.py` — JSON-LD, Open Graph meta, fallback, double-encoded HTML entities
- `test_llm_extractor.py` — mocked Gemini, malformed JSON, markdown fences
- `test_form_filler.py` — profile loading, mocked mapping, base64 CV reader
- `test_cv_tailor.py` — slug normalisation, filename convention, DOCX parsing
  (paragraphs + tables), output path resolution, full orchestration with
  mocked Gemini + mocked PDF rendering, error paths, summary generation
  (cleaning, quote/fence stripping, empty/short/error fallback, no-API-key
  short-circuit), `_inject_summary` placement, `include_summary` toggle,
  banned-cliché audit on a sampled output path

## Supported sites

The extension is active on **all sites** (`<all_urls>` in the manifest).
JSON-LD scraping natively covers HelloWork, Indeed, WTTJ. LinkedIn and
JobTeaser go through site-specific selectors plus the text fallback.

## Technical notes

- **Encoding** — Windows PowerShell reads `.ps1` files as CP-1252 by default.
  `dev.ps1` is pure ASCII to avoid breakage.
- **uvicorn `--reload`** only watches `.py` files, not `.env`. If you change
  the API key, restart the backend (Ctrl+C + relaunch, or just re-run
  `dev.ps1` which kills orphans). Otherwise the uvicorn watcher can respawn
  a child worker with the stale environment.
- **Gemini model** — `gemini-2.0-flash` was demoted from the free tier. Use
  `gemini-2.5-flash` (default) or `gemini-2.5-flash-lite` for more headroom.
- **React-controlled inputs** — the content script uses
  `Object.getOwnPropertyDescriptor` to call the native setter and bypass
  React/Vue intercepting `.value`.
- **CV upload** — `<input type=file>` is filled via `DataTransfer` + `File`.
  Works on most modern forms; can be blocked by strict validations relying
  on the `isTrusted` event flag.
- **CV tailoring** — input is `.docx` only (parsed via `python-docx`).
  `.doc` legacy is not supported. Slugging strips accents (`L'Oréal` →
  `LOreal`), normalises separators to `_`, and lowercases the filename
  while keeping the company folder cased (`BNP_Paribas/`).

## Status

Functional MVP. The scrape + LLM pipeline runs on HelloWork / Indeed / WTTJ.
LinkedIn renders the structured fields but the Easy Apply form hasn't been
tested end-to-end. V1 of the form filler covers text, textarea, select,
checkbox, radio and file inputs; ATS coverage to validate on Workday,
Greenhouse, Lever.

## License

Personal project, no open-source license yet.

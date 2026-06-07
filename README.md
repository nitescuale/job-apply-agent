# Job Apply Agent

Chrome extension that analyses a job posting, extracts the essentials via LLM,
and auto-fills the application form from a locally stored user profile.

> Click on a job posting, the extension scrapes, filters, structures, and
> offers to fill the application form. You review and you submit.

The product UI is in French, but the codebase, docs and commit history are
in English.

---

## Stack

- **Extension** — React 18 + TypeScript, Vite 5 + `@crxjs/vite-plugin` (Manifest V3)
- **Backend** — Python 3.11+, FastAPI, uvicorn, [Scrapling](https://github.com/D4Vinci/Scrapling) (HTML parser)
- **LLM** — Google Gemini (`google-genai`), `gemini-2.5-flash`, free tier
- **Design** — "Atelier" light, Hanken Grotesk + Spline Sans Mono, single green accent (`#3d7d5a`)

## Architecture

```
job-apply-agent/
├── extension/                       Chrome Extension (Vite + CRXJS)
│   └── src/
│       ├── content/scraper.ts       Captures HTML, detects + fills the form
│       └── popup/Popup.tsx          UI, 4 states: idle → scraping → ready → applied
├── backend/
│   ├── main.py                      FastAPI: /health, /scrape-job, /fill-form
│   ├── agents/
│   │   ├── job_scraper.py           Scrapling: JSON-LD → meta → text fallback
│   │   ├── llm_extractor.py         Gemini: filters noise, structures essentials
│   │   └── form_filler.py           Gemini: maps form_schema + profile → values
│   └── data/
│       ├── user_profile.example.json
│       └── user_profile.json        (gitignored, your real profile)
├── tests/                           pytest (25 green tests)
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
4. If the page contains an application form: **Postuler** button (shortcut
   `Ctrl ↵` on Windows/Linux, `⌘ ↵` on macOS)
5. The extension fills the fields (highlighted in amber) — you review and
   submit yourself

## Endpoints

| Method | Route          | Description                                            |
|--------|----------------|--------------------------------------------------------|
| GET    | `/health`      | `{status, llm_available, form_filler_available}`       |
| POST   | `/scrape-job`  | Body: `{job_url, job_html}` → structured fields        |
| POST   | `/fill-form`   | Body: `{form_schema, context}` → `{values, cv_base64}` |

## Tests

```powershell
pytest -q
```

25 tests:
- `test_job_scraper.py` — JSON-LD, Open Graph meta, fallback, double-encoded HTML entities
- `test_llm_extractor.py` — mocked Gemini, malformed JSON, markdown fences
- `test_form_filler.py` — profile loading, mocked mapping, base64 CV reader

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

## Status

Functional MVP. The scrape + LLM pipeline runs on HelloWork / Indeed / WTTJ.
LinkedIn renders the structured fields but the Easy Apply form hasn't been
tested end-to-end. V1 of the form filler covers text, textarea, select,
checkbox, radio and file inputs; ATS coverage to validate on Workday,
Greenhouse, Lever.

## License

Personal project, no open-source license yet.

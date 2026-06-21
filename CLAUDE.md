# CLAUDE.md — Job Apply Agent

## Objectif

Extension Chrome React/TypeScript qui s'active sur n'importe quelle page.
Au clic, le content script capture le HTML rendu et l'envoie à un backend
FastAPI. Le backend :

1. extrait les infos pratiques de l'offre via Scrapling (JSON-LD `JobPosting`
   → meta Open Graph → sélecteurs site-specific → fallback texte) puis affine
   le résultat via Gemini (filtre + structure : titre, entreprise, skills,
   missions, summary).
2. génère un CV adapté à l'offre depuis un DOCX de base + le profil
   utilisateur, rendu en PDF ATS-friendly via xhtml2pdf.
3. détecte le formulaire de candidature et le remplit (text, select, radio,
   checkbox, file) à partir du profil + contexte de l'offre via Gemini.

La popup (design "Atelier" clair) orchestre ces trois passes.

## Architecture

```
job-apply-agent/
├── extension/                      Chrome Extension React + TS (Vite + CRXJS)
│   └── src/
│       ├── content/scraper.ts      capture HTML, detect_form, fill_form
│       │                           (React-safe setter, DataTransfer pour files)
│       └── popup/Popup.tsx         UI Atelier, 7 états :
│                                   idle → scraping → ready → applying →
│                                   applied | error | apply-error
├── backend/
│   ├── main.py                     FastAPI : /health, /scrape-job,
│   │                               /fill-form, /tailor-cv
│   ├── agents/
│   │   ├── job_scraper.py          Scrapling : JSON-LD → meta → site → texte
│   │   ├── llm_extractor.py        Gemini : filtre/structure l'offre
│   │   ├── form_filler.py          Gemini : profil + form_schema → values
│   │   └── cv_tailor.py            Gemini (2 passes : summary + CV) +
│   │                               python-docx + xhtml2pdf → PDF
│   └── data/
│       ├── user_profile.example.json
│       └── user_profile.json       gitignoré (profil réel)
├── tests/                          pytest (55 tests verts)
│   ├── test_job_scraper.py
│   ├── test_llm_extractor.py
│   ├── test_form_filler.py
│   └── test_cv_tailor.py
├── design_handoff_atelier/         référence design système Atelier
├── dev.ps1                         lance backend + Vite (Windows)
└── REPORT.md
```

## Stack technique

- **Extension** : React 18, TypeScript, Vite 5, CRXJS (`@crxjs/vite-plugin`),
  Manifest V3
- **Backend** : Python 3.11+ (3.10 ok), FastAPI, uvicorn, **Scrapling** (HTML)
- **LLM** : Google Gemini (`google-genai`), `gemini-2.5-flash`, tier gratuit
- **CV pipeline** : `python-docx` (DOCX in), `markdown` + `xhtml2pdf` (PDF out,
  pure Python — pas de dépendance système, contrairement à WeasyPrint qui
  exige GTK/Pango sous Windows)
- **Design** : Hanken Grotesk + Spline Sans Mono + JetBrains Mono, fond
  `#f7f7f5`, accent vert `#3d7d5a`

## Pipelines

### 1. `/scrape-job` — analyse de l'offre

1. **`job_scraper.scrape_job(html, url)`** — cascade : JSON-LD `JobPosting`
   (gère `@graph` imbriqué, `html.unescape` en boucle pour décoder le
   double-encoding), meta Open Graph, sélecteurs site-specific
   (LinkedIn/HelloWork/Indeed/WTTJ/JobTeaser), fallback `<title>` + texte
   du `<body>` (≤ 15000 chars, nav/footer/scripts retirés).
2. **`llm_extractor.extract_essentials(scraped)`** — Gemini renvoie un JSON
   `{title, company, location, contract_type, salary, remote,
   experience_level, skills[], missions[], summary}`. Étape optionnelle :
   sans `GEMINI_API_KEY` ou sur erreur, le backend renvoie le scraping brut
   (`llm_used: false`).
3. `result["url"] = request.job_url` réaffirmé après le merge LLM (défense).

### 2. `/tailor-cv` — CV adapté en PDF

1. `profile.base_cv_path` (DOCX) → `python-docx` extrait le texte
   (paragraphes + tables).
2. **Passe Gemini #1 — SUMMARY** (si `profile.include_summary`, défaut `true`) :
   2-3 phrases avec règles dures — uniquement des faits issus du profil/CV
   base, 2-4 keywords de l'offre mirrorés, clichés interdits centralisés
   dans `BANNED_CLICHES` (passionate, team player, fast learner…), pas
   d'ambitions disqualifiantes. Échec / output trop court → `None`, pas de
   crash, `summary_used: false`.
3. **Passe Gemini #2 — CV** : reçoit le CV base + l'offre + le profil,
   renvoie un Markdown anglais qui mirror les keywords pour l'ATS, réordonne
   les bullets, n'invente jamais dates ni titres, vise ~600 mots. Cette
   passe ne produit PAS de section Summary (le prompt l'interdit) — le
   summary est injecté ensuite via `_inject_summary` avant le premier `## `.
4. Markdown → HTML → **xhtml2pdf** PDF (A4, Helvetica 10.5pt, marges 1.6cm,
   sections uppercase letter-spaced, mono-colonne ATS-friendly).
5. Sauvegarde : `{cv_output_dir}/{Company_Sanitized}/0_cv_firstname_lastname_jobtitle_company.pdf`.
   `_slug` normalise via `unicodedata.NFKD` + regex (`L'Oréal` → `LOreal`).

### 3. `/fill-form` — remplissage du formulaire

1. Content script `DETECT_FORM` → schéma `{fields: [{id, label, type,
   options?, required}]}`.
2. `form_filler.fill_form(form_schema, context)` — Gemini reçoit le schéma +
   profil + contexte (title/company de l'offre pour interpoler la lettre),
   renvoie `{field_id: value}` sous règles strictes : ne jamais inventer,
   respecter les options de select/radio, interpoler `{title}`/`{company}`
   dans la lettre.
3. Content script `FILL_FORM` : `setNativeValue` via
   `Object.getOwnPropertyDescriptor` pour bypasser React/Vue, `DataTransfer`
   + `File` pour `<input type=file>` (CV en base64). Surlignage ambre sur
   les champs remplis.
4. Renvoie `{filled, skipped}` affiché dans la popup.

## API Backend — Endpoints

```
GET  /health         → {status, llm_available, form_filler_available,
                        cv_tailor_available}

POST /scrape-job     body: {job_url, job_html}
                     → champs offre fusionnés + {llm_used, llm_error?}

POST /tailor-cv      body: {offer: {title, company, ...}}
                     → {saved_path, filename, folder, markdown, summary_used}

POST /fill-form      body: {form_schema, context?}
                     → {values: {field_id: value}, cv_base64?}
```

Codes d'erreur :
- `412` ressource absente (profil, CV base introuvable)
- `422` validation (HTML/URL vide, offre incomplète)
- `502` LLM en erreur (clé invalide, 429, JSON mal formé)
- `504` timeout (15s scraping, 30s LLM, 60s tailoring)
- `500` erreur inattendue

## Extension Chrome

- **Manifest V3**, `host_permissions: ["<all_urls>"]`,
  `content_scripts.matches: ["<all_urls>"]`
- **Popup** : 7 états, raccourci `Ctrl ↵` (Windows/Linux) / `⌘ ↵` (Mac)
  pour déclencher "Postuler". Boutons : Analyser, Adapter le CV, Postuler.
- **Communication** : popup → `chrome.tabs.sendMessage` →
  content (`CAPTURE_JOB_HTML` / `DETECT_FORM` / `FILL_FORM`) →
  fetch → backend `localhost:8000`
- **Dev** : Vite 5173, CRXJS HMR. `vite.config.ts` force `host: 'localhost'`
  + CORS pour origines `chrome-extension://*` (sinon CRXJS bind IPv6 et
  Chrome appelle IPv4 → connection refused).

## Builds

- `npm run build` (depuis `extension/`) → `extension/dist/` — Chrome MV3,
  service worker module CRXJS.
- `npm run build:firefox` → invoque `scripts/build-firefox.mjs` qui :
  1. lance `vite build`
  2. copie `dist/` → `dist-firefox/`
  3. patche le manifest pour Firefox MV3 :
     - ajoute `browser_specific_settings.gecko.{id, strict_min_version: "121.0",
       data_collection_permissions: { required: ["none"] }}`
     - convertit `background.service_worker` (module ESM) en
       `background.scripts` (script classique) en pointant directement
       sur le bundle `assets/background.ts-*.js` et en supprimant le
       loader CRXJS qui utilisait `import`.
  4. valide le manifest reparsé + check `web-ext lint` propre (0
     erreur, 0 notice ; 2 warnings React `innerHTML` attendus).
- Chargement Firefox : `about:debugging#/runtime/this-firefox` →
  "Load Temporary Add-on" → pointer un fichier dans `dist-firefox/`.
  Tient jusqu'au prochain restart de Firefox (limitation des temporary
  add-ons).
- Caveat Firefox : `chrome.tabs.create({url: "file:///..."})` (ouverture
  du PDF tailored) peut être bloqué par la sandbox profil. Sur Chrome
  ça marche tel quel ; sur Firefox il faut accepter l'accès aux fichiers
  locaux lors du premier prompt.

## Règles importantes

- **Logger** chaque appel API dans `backend/logs/{date}.log` (INFO + erreurs).
- **CORS** : FastAPI accepte `*`, `allow_credentials=False` (requis avec
  wildcard).
- **Timeouts** : 15s scrape, 30s LLM extract / fill-form, 60s tailor-cv.
- **Clé API** : `GEMINI_API_KEY` dans `.env`, jamais hardcodée, jamais
  loggée. Créer sur https://aistudio.google.com/apikey.
- **uvicorn `--reload`** ne watch pas `.env` : changer la clé impose un
  redémarrage complet (`dev.ps1` tue les orphelins sur 8000/5173).
- **Tests** : un fichier par agent dans `tests/`, mocks Gemini + xhtml2pdf.
- **Encoding** : `dev.ps1` en ASCII pur (Windows PowerShell lit CP-1252 par
  défaut).
- **Profil utilisateur** : `backend/data/user_profile.json` est gitignoré.
  Modèle : `user_profile.example.json`.

## Variables d'environnement (`.env`)

- `GEMINI_API_KEY` — clé Google Gemini (vide → toutes les étapes LLM
  désactivées, dégradation propre)
- `GEMINI_MODEL` — défaut `gemini-2.5-flash` (`gemini-2.0-flash` a été
  démoté hors free tier)
- `BACKEND_PORT`, `LOG_LEVEL`

## Format des commits

`feat(scope): description`
Ex : `feat(scraper): handle JSON-LD @graph nested JobPosting`,
`feat(cv): tailored SUMMARY pass with strict factual guardrails`,
`fix(form): React-safe native value setter`.

## Definition of Done

- [ ] Code fonctionnel et testé manuellement sur ≥1 site cible
- [ ] Tests unitaires (pytest) couvrent JSON-LD, meta, fallback, erreurs,
      LLM mocké, slug, filename, summary cleaning, banned-cliché audit
- [ ] Docstrings sur les fonctions publiques
- [ ] Logs sur les étapes clés
- [ ] `npx tsc --noEmit` propre (extension)

## En fin de session — mettre à jour REPORT.md

Avec : ce qui est implémenté, ce qui reste, décisions d'archi, blocages.
Bannière "Superseded" sur les entrées rendues obsolètes par un pivot.

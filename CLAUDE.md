# CLAUDE.md — Job Apply Agent (MVP)

## Objectif MVP

Extension Chrome React/TypeScript qui s'active sur n'importe quelle page.
Au clic, le content script capture le HTML rendu et l'envoie à un backend
FastAPI. Le backend extrait les infos pratiques via Scrapling
(JSON-LD `JobPosting` → meta Open Graph → fallback texte), puis affine le
résultat via une moulinette LLM (Google Gemini) qui filtre le bruit et
structure l'essentiel. La popup affiche les champs nettoyés.

## Architecture

```
job-apply-agent/
├── extension/                    Chrome Extension React + TypeScript (Vite + CRXJS)
│   └── src/
│       ├── content/scraper.ts    Capture document.documentElement.outerHTML
│       └── popup/Popup.tsx       Bouton + affichage des champs extraits
├── backend/
│   ├── main.py                   FastAPI : /health, /scrape-job
│   └── agents/
│       ├── job_scraper.py        Scrapling : JSON-LD → meta → fallback texte
│       └── llm_extractor.py      Moulinette Gemini : filtre + structure l'essentiel
├── tests/
│   ├── test_job_scraper.py
│   └── test_llm_extractor.py
└── REPORT.md
```

## Stack technique

- **Extension** : React 18, TypeScript, Vite 5, CRXJS (`@crxjs/vite-plugin`)
- **Backend** : Python 3.11+, FastAPI, uvicorn, **Scrapling** (parser HTML)
- **LLM** : Google Gemini (`google-genai`), modèle flash, tier gratuit

## Pipeline (`/scrape-job`)

1. **`job_scraper.scrape_job(html, url)`** — extraction structurelle en cascade :
   JSON-LD `JobPosting` (gère `@graph` imbriqué, décode les entités HTML) →
   meta Open Graph → sélecteurs spécifiques par site → fallback `<title>` /
   texte du `<body>` (≤ 15000 chars, nav/footer/scripts ignorés).
2. **`llm_extractor.extract_essentials(scraped)`** — passe le scraping brut à
   Gemini qui renvoie un JSON propre : `title, company, location, contract_type,
   salary, remote, experience_level, skills[], missions[], summary`.
   Étape **optionnelle** : si `GEMINI_API_KEY` absente ou erreur, le backend
   renvoie le scraping brut (`llm_used: false`).

## API Backend — Endpoints

```
GET  /health         response: {status: "ok", llm_available: bool}
POST /scrape-job     body: {job_url: str, job_html: str}
                     response: champs fusionnés (scraping + LLM) + llm_used: bool
```

## Extension Chrome

1. Active sur **tous les sites** (`<all_urls>` dans le manifest)
2. Popup React, 4 états : idle → loading → result → error
3. Content script : `document.documentElement.outerHTML` (HTML rendu post-JS),
   tronqué à 1.5 MB, message `CAPTURE_JOB_HTML`
4. Communication : popup → `chrome.tabs.sendMessage` → content → fetch → backend `localhost:8000`

## Règles importantes

- **Logger** chaque appel API dans `backend/logs/{date}.log`
- **CORS** : FastAPI accepte `*` (extension Chrome en dev)
- **Timeout** : 15s scraping + 30s LLM sur `/scrape-job`
- **Clé API** : `GEMINI_API_KEY` dans `.env` (jamais hardcodée), créée sur
  https://aistudio.google.com/apikey
- **Tests** : un fichier de test par agent dans `tests/`

## Variables d'environnement (`.env`)

- `GEMINI_API_KEY` — clé Google Gemini (vide = étape LLM désactivée)
- `GEMINI_MODEL` — modèle Gemini (défaut `gemini-2.0-flash`)
- `BACKEND_PORT`, `LOG_LEVEL`

## Format des commits

`feat(scope): description`
Ex : `feat(scraper): handle JSON-LD @graph nested JobPosting`

## Definition of Done

- [ ] Code fonctionnel et testé manuellement sur ≥1 site cible
- [ ] Tests unitaires (pytest) couvrent JSON-LD, meta, fallback, erreurs, LLM mocké
- [ ] Docstrings sur les fonctions publiques
- [ ] Logs sur les étapes clés

## En fin de session — générer REPORT.md

Avec : ce qui est implémenté, ce qui reste, décisions d'archi, blocages.

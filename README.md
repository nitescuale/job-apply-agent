# Job Apply Agent

Extension Chrome qui analyse une offre d'emploi, en extrait les essentiels via
LLM, et remplit automatiquement le formulaire de candidature à partir d'un
profil utilisateur stocké en local.

> Tu cliques sur une offre, l'extension scrape, filtre, structure, et propose
> de remplir le formulaire de candidature. Tu relis, tu envoies.

---

## Stack

- **Extension** — React 18 + TypeScript, Vite 5 + `@crxjs/vite-plugin` (Manifest V3)
- **Backend** — Python 3.11+, FastAPI, uvicorn, [Scrapling](https://github.com/D4Vinci/Scrapling) (parser HTML)
- **LLM** — Google Gemini (`google-genai`), `gemini-2.5-flash`, tier gratuit
- **Design** — Atelier light, Hanken Grotesk + Spline Sans Mono, un seul accent vert (`#3d7d5a`)

## Architecture

```
job-apply-agent/
├── extension/                       Chrome Extension (Vite + CRXJS)
│   └── src/
│       ├── content/scraper.ts       Capture HTML, détecte + remplit le form
│       └── popup/Popup.tsx          UI 4 états : idle → scraping → ready → applied
├── backend/
│   ├── main.py                      FastAPI : /health, /scrape-job, /fill-form
│   ├── agents/
│   │   ├── job_scraper.py           Scrapling : JSON-LD → meta → fallback texte
│   │   ├── llm_extractor.py         Gemini : filtre + structure l'essentiel
│   │   └── form_filler.py           Gemini : mappe form_schema + profil → valeurs
│   └── data/
│       ├── user_profile.example.json
│       └── user_profile.json        (gitignoré, c'est le vrai profil)
├── tests/                           pytest (25 tests verts)
├── dev.ps1                          Lance backend + Vite en parallèle (Windows)
└── design_handoff_atelier/          Référence design "Atelier"
```

## Pipeline

### 1. Analyse d'offre — `POST /scrape-job`

```
HTML rendu (depuis le content script)
        ↓
Scrapling : JSON-LD JobPosting (@graph aware) → meta Open Graph
            → sélecteurs site-specific → fallback <title> / <body>
        ↓
LLM Gemini (optionnel) : filtre le bruit, structure title, company,
        location, contract_type, salary, remote, experience_level,
        skills[], missions[], summary
        ↓
Réponse fusionnée → popup affiche le résultat (Atelier UI)
```

Si `GEMINI_API_KEY` absente ou erreur API, l'étape LLM est skipée
proprement (`llm_used: false`).

### 2. Auto-remplissage — `POST /fill-form`

```
Content script DETECT_FORM → schéma {fields: [{id, label, type, ...}]}
        ↓
Gemini : reçoit form_schema + user_profile + contexte (title, company),
         retourne {field_id: value} en respectant les règles strictes
         (n'invente rien, respect des options select/radio, injection
         de {title}/{company} dans la lettre de motivation)
        ↓
Content script FILL_FORM : setter natif React-safe, trick DataTransfer
         pour <input type=file>, surlignage ambre des champs remplis
        ↓
Rapport {filled, skipped} affiché dans la popup
```

## Démarrage rapide

### Pré-requis

- Node.js 18+
- Python 3.11+ (testé sur 3.10/3.11)
- Une clé Gemini gratuite : <https://aistudio.google.com/apikey>

### 1. Cloner et installer

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

### 2. Configurer

Crée un `.env` à la racine :

```
GEMINI_API_KEY=ta_cle_ici
GEMINI_MODEL=gemini-2.5-flash
```

Copie le template de profil et adapte-le :

```powershell
Copy-Item backend\data\user_profile.example.json backend\data\user_profile.json
# Édite backend\data\user_profile.json (ignored par git)
```

### 3. Lancer

```powershell
.\dev.ps1
```

Le script tue tout process orphelin sur 8000/5173 puis ouvre deux fenêtres :
- **Backend** sur <http://localhost:8000> (uvicorn --reload)
- **Vite** sur <http://localhost:5173> (HMR)

### 4. Charger l'extension dans Chrome

1. `chrome://extensions/`
2. Active **Mode développeur**
3. **Charger l'extension non empaquetée** → sélectionne `extension/dist/`

Tant que Vite tourne, les modifs TS/TSX/CSS sont hot-reloadées.
Pour les changements de `manifest.json`, recharge l'extension manuellement.

## Utilisation

1. Ouvre une offre d'emploi (LinkedIn, HelloWork, Indeed, WTTJ, JobTeaser, etc.)
2. Clique sur l'icône de l'extension → bouton **Analyser la page**
3. La popup affiche l'offre structurée : titre, entreprise, lieu, contrat, salaire, expérience, dates, description
4. Si la page contient un formulaire de candidature : bouton **Postuler** (raccourci `Ctrl ↵` / `⌘ ↵`)
5. L'extension remplit les champs (surlignés en ambre) — tu relis et tu cliques Envoyer toi-même

## Endpoints

| Méthode | Route          | Description                                     |
|---------|----------------|-------------------------------------------------|
| GET     | `/health`      | `{status, llm_available, form_filler_available}` |
| POST    | `/scrape-job`  | Body : `{job_url, job_html}` → champs structurés |
| POST    | `/fill-form`   | Body : `{form_schema, context}` → `{values, cv_base64}` |

## Tests

```powershell
pytest -q
```

25 tests :
- `test_job_scraper.py` — JSON-LD, meta, fallback, double-encoding HTML entities
- `test_llm_extractor.py` — Gemini mocké, JSON malformé, fences markdown
- `test_form_filler.py` — profil chargé, mapping mocké, base64 CV

## Sites supportés

L'extension est active sur **tous les sites** (`<all_urls>` dans le manifest).
Le scraper JSON-LD couvre nativement HelloWork, Indeed, WTTJ. LinkedIn et
JobTeaser passent par les sélecteurs site-specific + fallback texte.

## Notes techniques

- **Encodage** : Windows PowerShell lit les `.ps1` en CP-1252 par défaut.
  `dev.ps1` est en ASCII pur pour éviter la casse.
- **uvicorn `--reload`** ne surveille que les `.py`, pas le `.env`. Si tu changes
  la clé API, redémarre le backend (Ctrl+C + relance ou `dev.ps1` qui kill les
  orphelins). Sinon le watcher de uvicorn peut respawn un worker enfant avec
  l'ancien env.
- **Modèle Gemini** : `gemini-2.0-flash` a été retiré du tier gratuit. Utilise
  `gemini-2.5-flash` (défaut) ou `gemini-2.5-flash-lite` pour plus de quota.
- **React-controlled inputs** : le content script utilise `Object.getOwnPropertyDescriptor`
  pour appeler le setter natif et bypasser React/Vue qui interceptent `.value`.
- **Upload CV** : `<input type=file>` est rempli via `DataTransfer` + `File`. Marche
  sur la plupart des formulaires modernes, peut être bloqué par les validations
  strictes basées sur `isTrusted`.

## Statut

MVP fonctionnel. Le pipeline scrape + LLM tourne sur HelloWork / Indeed / WTTJ.
LinkedIn rend les champs structurés mais le formulaire Easy Apply n'a pas été
testé end-to-end. La V1 du form-filler remplit les champs textuels, selects et
checkboxes ; à valider sur plus d'ATS (Workday, Greenhouse, Lever).

## Licence

Personnel, pas de licence open-source pour l'instant.

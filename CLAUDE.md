# CLAUDE.md — Job Apply Agent

## Objectif

Extension Chrome/Firefox React/TypeScript qui s'active sur n'importe quelle
page. Au clic, le content script capture le HTML rendu et l'envoie à un
backend FastAPI. Le backend :

1. extrait les infos pratiques de l'offre via Scrapling (JSON-LD `JobPosting`
   → meta Open Graph → sélecteurs site-specific → fallback texte) puis affine
   le résultat via Gemini (filtre + structure : titre, entreprise, skills,
   missions, summary). Dédup et cache via SQLite (`store.py`) sur un hash
   canonical `title|company|location`.
2. génère un CV adapté à l'offre en éditant **en place** le DOCX source
   de l'utilisateur (forme inchangée, seuls SUMMARY + "Relevant coursework"
   sont tailorés), puis convertit en PDF via Word/LibreOffice.
3. détecte le formulaire de candidature et le remplit (text, select, radio,
   checkbox, file) à partir du profil + contexte de l'offre via Gemini.

La popup (design "Atelier" clair) orchestre ces trois passes via le
**background service worker**, qui survit à la fermeture du popup et écrit
son état dans `chrome.storage.local`.

## Architecture

```
job-apply-agent/
├── extension/                      Chrome/Firefox Extension React + TS
│   └── src/
│       ├── background.ts           service worker : exécute scrape/tailor/
│       │                           fill + PATCH application status. Écrit
│       │                           dans chrome.storage.local. Auto-marque
│       │                           applied après fill-form success.
│       ├── content/scraper.ts      capture HTML, detect_form, fill_form
│       │                           (React-safe setter, DataTransfer pour files)
│       ├── shared/status.ts        source of truth APPLICATION_STATUSES +
│       │                           STATUS_LABELS, partagé popup ↔ tracker
│       ├── popup/Popup.tsx         UI Atelier, hydrate chrome.storage.local
│       │                           et écoute onChanged. États :
│       │                           idle → scraping → ready → applying →
│       │                           applied | error | apply-error.
│       │                           Status éditable via dropdown + bouton
│       │                           "Suivi" qui ouvre le tracker.
│       └── tracker/                page plein écran, ouverte via
│           ├── index.html          chrome.tabs.create(getURL(...)). Liste
│           ├── index.tsx           toutes les candidatures groupées par
│           └── Tracker.tsx         société, status + notes éditables
│                                   inline avec PATCH /applications/{id}.
├── backend/
│   ├── main.py                     FastAPI + lifespan (init DB), endpoints
│   │                               /health, /scrape-job, /fill-form,
│   │                               /tailor-cv, /open-file, /applications*
│   ├── store.py                    SQLite stdlib : tables applications +
│   │                               scrapes, dédup via job_hash sha256,
│   │                               cache scrapes pour éviter re-LLM
│   ├── agents/
│   │   ├── job_scraper.py          Scrapling : JSON-LD → meta → site → texte
│   │   ├── llm_extractor.py        Gemini : filtre/structure l'offre
│   │   ├── form_filler.py          Gemini : profil + form_schema → values.
│   │   │                           Adapte qa_bank (banque de réponses
│   │   │                           canoniques : disponibilité, salaire,
│   │   │                           visa, ...) au lieu de régénérer à froid.
│   │   ├── match_scorer.py         Gemini + fallback overlap déterministe
│   │   │                           → {score 0-100, matched_skills,
│   │   │                              missing_skills, rationale, llm_used}
│   │   ├── pdf_convert.py          DOCX -> PDF (docx2pdf -> soffice).
│   │   │                           Extrait pour réutilisation cv_tailor +
│   │   │                           cover_letter sans cycle d'import.
│   │   ├── ats_lint.py             Déterministe (pas de LLM) — extrait le
│   │   │                           PDF via pdfminer.six et calcule un score
│   │   │                           ATS + suggestions (parsabilité, coverage
│   │   │                           skills, sections, longueur, contact).
│   │   ├── cover_letter.py         Gemini : génère une lettre long-form
│   │   │                           dans la langue de l'offre, builds DOCX
│   │   │                           via python-docx, convertit via
│   │   │                           pdf_convert. Accepte un `match` optionnel.
│   │   └── cv_tailor.py            python-docx édite en place uniquement
│   │                               SUMMARY + "Relevant coursework",
│   │                               Gemini renvoie {idx: new_text},
│   │                               pdf_convert (Word/Office) → PDF.
│   │                               Accepte un `match` optionnel pour orienter
│   │                               le prompt (sans inventer les missing).
│   └── data/
│       ├── user_profile.example.json
│       ├── user_profile.json       gitignoré (profil réel)
│       └── applications.db         gitignoré (SQLite tracking + cache)
├── scripts/
│   └── build-firefox.mjs           build + patch manifest pour Firefox MV3
├── tests/                          pytest (96 tests verts)
│   ├── test_job_scraper.py
│   ├── test_llm_extractor.py
│   ├── test_form_filler.py
│   ├── test_cv_tailor.py           36 tests
│   └── test_store.py               25 tests (DB, hash, cache)
├── design_handoff_atelier/         référence design système Atelier
├── dev.ps1                         lance backend + Vite (Windows)
└── REPORT.md
```

## Stack technique

- **Extension** : React 18, TypeScript, Vite 5, CRXJS (`@crxjs/vite-plugin`),
  Manifest V3 (Chrome + Firefox)
- **Backend** : Python 3.11+ (3.10 ok), FastAPI, uvicorn, **Scrapling** (HTML)
- **Persistance** : `sqlite3` stdlib (pas de SQLAlchemy/Alembic, CREATE TABLE
  IF NOT EXISTS, idempotent au démarrage via lifespan FastAPI)
- **LLM** : Google Gemini (`google-genai`), `gemini-2.5-flash`, tier gratuit
- **CV pipeline** : `python-docx` édite **en place** le DOCX source (texte
  des runs remplacé, formatting/styles préservés), puis `docx2pdf` convertit
  en PDF via Word COM (qualité maximale) avec fallback LibreOffice headless.
  Le DOCX généré et le PDF sont sauvegardés côte à côte.
- **Design** : Hanken Grotesk + Spline Sans Mono + JetBrains Mono, fond
  `#f7f7f5`, accent vert `#3d7d5a`

## Persistance — deux couches indépendantes

### 1. Côté extension : `chrome.storage.local` (état du popup)

Problème : un popup MV3 est détruit dès que l'utilisateur clique en dehors.
Sans persistance, l'analyse en cours est perdue et un re-clic redémarre
tout.

Solution : **toute la logique tourne dans `background.ts`** (service worker).
Le popup n'envoie qu'un message de déclenchement (`START_ANALYZE`,
`START_TAILOR_CV`, `START_FILL_FORM`, `RESET_STATE`), puis :

- **hydrate** `chrome.storage.local[STORAGE_KEY]` à l'ouverture pour
  retrouver le dernier état connu (résultat scraping, PDF généré, erreur…).
- **subscribe** à `chrome.storage.onChanged` pour réagir aux écritures du
  service worker pendant qu'il est ouvert.

Schéma de l'état stocké (clé `job-apply-popup-state`) :
```
{
  status:      'idle'|'scraping'|'ready'|'applying'|'applied'|'error'|'apply-error',
  result:      objet scrape (offre + flags from_cache/seen_before/application_status),
  error, applyError, fillReport,
  cvState:     'idle'|'generating'|'done'|'error',
  cvResult, cvError,
  inflight:    { kind: 'scrape'|'apply'|'tailor', started_at: ms } | null
}
```

- **Stale detection** : si `inflight.started_at` > 90s, le popup coerce
  l'état en `error` (le service worker a probablement été tué par le
  navigateur). Évite de rester bloqué en "scraping…" indéfiniment.
- **Reset button** ↺ dans la TopBar envoie `RESET_STATE` → le worker
  `chrome.storage.local.remove(STORAGE_KEY)`.
- **Persiste à travers** : fermeture du popup, navigation entre onglets,
  ouverture d'un autre onglet. **Ne persiste pas** à travers un restart du
  navigateur (par design : on veut repartir propre).

Conséquence importante : si tu fermes le popup pendant une analyse, le
fetch continue côté worker et le résultat sera affiché à la prochaine
ouverture. C'est ce qui résout le bug "extension unfocus pendant analyse =
analyse perdue".

### 2. Côté backend : SQLite (suivi des candidatures + cache)

`backend/data/applications.db` — créée au démarrage par `store.init_db()`
(idempotent, `CREATE TABLE IF NOT EXISTS`). Deux tables :

#### Table `applications` — une ligne par offre vue

```
id, job_url, job_hash UNIQUE, company, title, location, contract_type,
status (seen|applied|followed_up|interview|response_pos|response_neg),
match_score, cv_path, cover_letter_path, notes, created_at, updated_at
```

- **Dédup via `job_hash`** : sha256 de `title|company|location` après
  normalisation NFKD + ASCII + collapse whitespace + lowercase. Garantit
  que "L'Oréal" et "L'Oreal", "Paris " et "Paris" hashent au même endroit.
- **`upsert_application`** :
  - **première vue** : INSERT avec `status='seen'`, `created_at=now`
  - **vue suivante** : UPDATE des champs scraping via `COALESCE(?, col)`
    pour ne **jamais** écraser une valeur connue avec NULL ; `status` et
    `created_at` ne sont PAS touchés (préserve une transition manuelle
    `applied` même si on re-scrape).
  - retourne `(application_id, was_new)`.
- **`update_application`** : PATCH partiel ; `status` validé contre
  `VALID_STATUSES` (sinon ValueError → 422).

#### Table `scrapes` — cache des essentials Gemini

```
job_hash PRIMARY KEY, essentials_json TEXT, created_at
```

- `get_cached_scrape(hash)` → hit retourne le JSON parsé, court-circuite
  l'appel Gemini dans `/scrape-job` (économise un appel rate-limité et
  rend l'UX quasi-instantanée sur une offre re-visitée).
- `save_scrape_cache(hash, essentials)` → upsert via `ON CONFLICT DO
  UPDATE` (ré-écrit l'entrée existante).
- Stockage `ensure_ascii=False` pour préserver les accents dans le JSON.

#### Effet sur l'UX

Sur `/scrape-job`, après chaque scrape on renvoie au popup :
- `from_cache` : true si on a sauté Gemini grâce au cache scrapes
- `seen_before` : true si l'application existait déjà (revisite)
- `application_status` : status actuel (`seen`, `applied`, …)
- `application_id` : id pour les patches ultérieurs

Le popup affiche un **dropdown de statut** dans l'état "ready" (toujours
visible dès que `application_id` est présent). Sélectionner une nouvelle
valeur envoie un message `PATCH_APPLICATION` au service worker qui appelle
`PATCH /applications/{id}` et reflète le statut dans `chrome.storage.local`
→ le badge se met à jour live.

#### Transitions de statut — deux chemins

1. **Automatique** : après un `/fill-form` qui réussit, le service worker
   appelle `PATCH /applications/{application_id}` avec `status='applied'`
   si un `application_id` est disponible dans `result`. Si l'utilisateur a
   rempli un formulaire sans scraper l'offre d'abord, il n'y a pas
   d'`application_id` → la transition est skippée silencieusement (l'user
   peut marquer manuellement depuis le tracker).
2. **Manuelle** :
   - depuis le popup → dropdown de statut dans l'état "ready"
   - depuis le tracker → dropdown sur chaque ligne + notes inline
     (debounce 700ms avant PATCH).

### 3. Tracker — page plein écran de suivi

`chrome-extension://<id>/src/tracker/index.html`, ouverte via
`OPEN_TRACKER` (bouton ▤ Suivi dans la TopBar du popup) ou directement
dans un onglet.

- Charge `GET /applications` au mount + bouton ↻ Recharger
- **Stats strip** : total, déjà postulé, relancées, entretiens, réponses +/-
- **Filtres** : chips de statut (Tous + chaque statut) + search texte
  (matche company/title/location en sous-chaîne, lowercase)
- **Liste groupée par société** (alphabétique, ordre des candidatures =
  `created_at` desc venant du backend) ; headers collapsibles
- **Chaque ligne** : titre, location, contrat, "vue il y a Xj", dropdown
  status (PATCH optimiste), textarea notes (debounce 700ms), liens
  ↗ offre (URL externe) et 📄 CV (passe par `/open-file`)
- Pas de couche `chrome.storage` : la page parle direct au backend
  (CORS `allow_origins="*"` côté FastAPI). Pas de WebSocket non plus —
  le bouton ↻ suffit.
- Build : déclaré en `rollupOptions.input.tracker` dans `vite.config.ts`
  (CRXJS ne traite pas automatiquement les HTML dans
  `web_accessible_resources` → le bundle TSX serait copié brut sans cette
  ligne). Listé dans `manifest.web_accessible_resources` pour que
  `chrome.runtime.getURL('src/tracker/index.html')` résolve.

## Pipelines

### 1. `/scrape-job` — analyse de l'offre (avec dédup + cache)

1. **`scrape_job(html, url)`** — cascade : JSON-LD `JobPosting` (gère
   `@graph` imbriqué, `html.unescape` en boucle pour décoder le
   double-encoding), meta Open Graph, sélecteurs site-specific
   (LinkedIn/HelloWork/Indeed/WTTJ/JobTeaser), fallback `<title>` + texte
   du `<body>` (≤ 15000 chars, nav/footer/scripts retirés).
2. **`compute_job_hash(title, company, location)`** sur les champs
   structurels pré-LLM.
3. **Cache hit** (`get_cached_scrape(hash)`) : on saute Gemini, on upsert
   l'application (au cas où première fois qu'on suit ce hash), on renvoie
   les essentials cached + `from_cache: true`.
4. **Cache miss** : `llm_extractor.extract_essentials(scraped)` renvoie un
   JSON `{title, company, location, contract_type, salary, remote,
   experience_level, skills[], missions[], summary}`. Sans `GEMINI_API_KEY`
   ou sur erreur, on dégrade (`llm_used: false`).
5. **`save_scrape_cache` + `upsert_application`** : on persiste le résultat
   et on crée/met-à-jour la ligne applications.
6. **Réaffirme `result["url"] = request.job_url`** après le merge LLM
   (défense contre un override Gemini).

### 2. `/tailor-cv` — CV adapté en PDF (scope strict)

Principe : on ne ré-écrit pas la mise en page. On charge **le DOCX
existant** de l'utilisateur, on identifie **uniquement** les paragraphes
de la section SUMMARY + la ligne "Relevant coursework" de la section
EDUCATION, on demande à Gemini une version tailorée de leur texte, on
remplace le texte des runs en gardant le formatting d'origine, et on
convertit le résultat en PDF.

1. `profile.base_cv_path` (DOCX) → `python-docx` charge le document et
   `_collect_paragraphs` parcourt les paragraphes top-level + ceux des
   cellules de table, dans l'ordre de lecture.
2. **`_collect_editable_in_sections`** — filtre section-aware :
   - parcourt linéairement les paragraphes
   - `_is_section_header` + `_normalize_section` détectent les en-têtes
     ALL-CAPS, mappés via `_SECTION_KEYWORDS` (SUMMARY/PROFILE/OBJECTIVE,
     EXPERIENCE/WORK, EDUCATION/ACADEMIC, …)
   - garde uniquement :
     - paragraphes substantiels (`_is_substantive`) sous la section
       courante SUMMARY
     - la ligne commençant par "Relevant coursework" sous EDUCATION
   - tout le reste (bullets EXPERIENCE, PROJECTS, SKILLS, LANGUAGES,
     CERTIFICATIONS) est **intouché**.
3. **Passe Gemini** : reçoit `{indices: texte original}` pour CES
   paragraphes uniquement + l'offre + le profil. Renvoie `{idx:
   nouveau_texte}` (±25% de longueur, ATS-keywords mirrorés, pas
   d'invention, clichés `BANNED_CLICHES` interdits). Omet les indices à
   laisser inchangés.
4. **`_set_paragraph_text`** : met le nouveau texte dans
   `paragraph.runs[0].text` et vide les autres runs. Le formatting du
   premier run (font, gras, italique, couleur, taille, alignement du
   paragraphe) est conservé. Compromis assumé : un paragraphe avec runs
   mixtes prend le style du premier run uniformément.
5. **`_convert_docx_to_pdf`** : `docx2pdf` (Word COM via pywin32 sur
   Windows — qualité PDF 1:1 avec le DOCX) puis fallback `soffice
   --headless --convert-to pdf` (LibreOffice). Si ni l'un ni l'autre n'est
   dispo, RuntimeError explicite.
6. **Nom de fichier** : `0_CV_{Firstname}_{Lastname}_{JobTitle}.pdf`
   (Title case, pas de company). `_canonical_job_title` strippe les
   parenthèses, les segments après `-`/`–`/`—`, et les markers `F/H`,
   `H/F`, `M/F`. `_slug_title` Title-case chaque token mais préserve les
   acronymes ALL-CAPS ≤ 4 chars (AI, ML, NLP, …). Exemple :
   `Deep Learning Algorithm Graduate (TikTok Search Ranking) — 2026 Start
   (BS/MS)` → `0_CV_Alexandru_Nitescu_Deep_Learning_Algorithm_Graduate.pdf`.
7. Sauvegarde côte à côte dans `{cv_output_dir}/{Company_Sanitized}/` :
   `0_CV_..._.docx` + `0_CV_..._.pdf`. Le DOCX intermédiaire est conservé
   pour permettre une retouche manuelle si Gemini misfire.
8. **Mise à jour SQLite** : `/tailor-cv` upsert l'application (au cas où
   on tailor sans avoir scrapé d'abord) et écrit `cv_path` sur la ligne.

Retour : `{saved_path, saved_docx_path, filename, folder, edited_count, editable_count}`.

### 2.bis. `/match-score` — pertinence offre / profil

Endpoint dédié + appel automatique du service worker après `/scrape-job`.
Le score est attaché à `result.match` dans `chrome.storage.local` et la
popup le rend dès qu'il arrive (carte avec jauge, rationale, chips des
missing_skills).

1. **`match_scorer.score_match(offer, profile)`** :
   - Si `GEMINI_API_KEY` présente → Gemini renvoie un JSON strict
     `{score, matched_skills, missing_skills, rationale}` (response_mime_type
     `application/json`, temperature 0).
   - Sinon, ou si Gemini fail (JSON invalide, réseau, rate limit) →
     **fallback déterministe** : overlap normalisé (NFKD + ASCII +
     lowercase) entre `offer.skills` et les skills du profil (skills peut
     être une liste OU un dict par catégorie). Score = `matched / total
     * 100`. Choix de l'overlap vs Jaccard : un candidat avec PLUS de
     skills que ce que l'offre demande ne doit pas être pénalisé.
   - **Ne lève jamais** vers l'appelant : toute erreur est loggée puis on
     retombe sur le fallback. Garantit une jauge toujours rendue.
2. **Persistance** : si `compute_job_hash(title, company, location)`
   matche une ligne existante de `applications`, on écrit `match_score`
   via `store.set_match_score(application_id, score)`. L'endpoint ne crée
   PAS d'application — c'est le rôle de `/scrape-job`.
3. **Auto-trigger** : `runAnalyze` dans le service worker, juste après
   `setState({status:'ready', result})`, fetch `/match-score` avec une
   sous-shape de l'offre (sans description ni url, pour la concision du
   prompt) et patch `result.match` au retour. La popup affiche d'abord un
   "Calcul du score…" (placeholder spinner) puis la carte complète.
4. **Réutilisation dans `/tailor-cv`** : si le service worker a un match
   dans `chrome.storage.local`, il le forward dans le body
   `{offer, match}`. `cv_tailor.tailor_cv(offer, match=None)` accepte le
   kwarg, injecte un bloc `--- MATCH ---` dans le prompt avec
   `matched_skills_emphasize_truthfully` et
   `missing_skills_do_not_claim_present`. Rétrocompat strict : sans
   match, le prompt est identique à avant.

### 2.ter. `/cover-letter` — lettre de motivation long-form

Endpoint dédié + bouton "Lettre de motivation" dans l'état `ready` du
popup. Pipeline mirror du CV mais sans template d'origine — on génère
intégralement le texte via Gemini.

1. **`cover_letter.generate_cover_letter(offer, profile, match=None) → str`** :
   - Prompt en anglais (Gemini suit mieux les contraintes structurelles
     en anglais), mais consigne explicite "LANGUAGE RULE: write in the
     SAME language as the OFFER" pour que la lettre sorte dans la langue
     de l'offre (FR si l'offre est en FR, EN sinon).
   - Structure imposée 3-4 paragraphes (~300-450 mots) : opening + why
     this role + why this company + closing.
   - Réutilise `BANNED_CLICHES` de cv_tailor (consigne dans le prompt +
     audit log warn si Gemini glisse un cliché malgré la règle).
   - Pas d'invention factuelle.
   - Bloc `--- MATCH ---` optionnel pour orienter les
     `matched_skills_emphasize_truthfully` /
     `missing_skills_do_not_claim_present`.
   - Raise sur clé absente ou réponse vide.
2. **`cover_letter._build_docx(text, profile, offer, docx_path)`** :
   python-docx assemble un DOCX minimaliste — header contact (nom bold +
   email/phone/city), date + destinataire, corps (split sur double
   newline), signature. Mise en page sobre, taille 10 sur le contact.
3. **`cover_letter.tailor_cover_letter(offer, match=None)`** :
   orchestrateur — résout le chemin de sortie, génère le texte, écrit le
   DOCX, convertit via `pdf_convert.convert_docx_to_pdf`.
4. **Filename** : `1_Cover_Letter_Firstname_Lastname_JobTitle.pdf`
   (préfixe `1_` pour trier après le CV `0_CV_*`). Réutilise
   `_canonical_job_title` + `_slug_title` de cv_tailor.
5. **Persistance** : l'endpoint upserte l'application si elle n'existe
   pas (cas "lettre générée sans scrape"), puis écrit
   `cover_letter_path` via `store.update_application`.
6. **`POST /cover-letter`** body `{offer, match?}` → `{text, saved_path,
   saved_docx_path, filename, folder}`. Timeout 60s, codes standards.

### 2.quinquies. `/ats-lint` — analyse ATS déterministe du PDF généré

Endpoint déterministe (pas de LLM) appelé automatiquement par le service
worker juste après `/tailor-cv` réussit. Le rapport est attaché à
`cvResult.ats` dans `chrome.storage.local` et la popup affiche un badge
sobre + un panneau dépliable.

1. **`ats_lint.lint_cv(pdf_path, offer)`** :
   - Extraction PDF via `pdfminer.six` (pure-Python, ajoutée en
     dépendance dans `requirements.txt`) — `extract_text` pour le contenu,
     `extract_pages` pour le compteur de pages.
   - Pondération du score (somme = 100) : `parsability` 30, `keyword_coverage`
     30 proportionnel (`round(coverage * 30)`), `section_experience` 8,
     `section_education` 8, `section_skills` 6, `length` 8, `contact_block` 10.
   - **Parsability** : on considère un PDF image-only si `< 200` chars
     extraits. Suggestion immédiate de régénérer depuis le DOCX source.
   - **Coverage** : dédup normalisée (NFKD + ASCII + lowercase) puis
     match avec word-boundary pour les skills mono-token (évite que "Go"
     matche "going"), sous-chaîne pour les multi-mots ("Machine Learning").
   - **Sections** : scan ligne-par-ligne, header ≤ 60 chars + comparaison
     ASCII-upper aux `_SECTION_KEYWORDS` (FR + EN). Tolérant aux accents
     ("EXPÉRIENCE" matche "EXPERIENCE"). Accepte les en-têtes longs
     ("EXPERIENCE PROFESSIONNELLE", "ACADEMIC BACKGROUND") tant que le
     mot-clé y apparaît.
   - **Contact** : regex permissive pour email, regex chiffre + séparateurs
     pour téléphone avec validation longueur (8-16 chiffres pour éviter
     que codes postaux / dates ne matchent).
   - **Ne lève jamais** sauf `FileNotFoundError` si le PDF n'existe pas.
     Une erreur d'extraction interne (`pdfminer` plante) → on retombe
     sur `(texte vide, 0 pages)` et le rapport reflète une parsability=False.
2. **`POST /ats-lint`** body `{pdf_path, offer}` :
   - Validation chemin **stricte** : extension `.pdf` ET path sous
     `profile.cv_output_dir` (`Path.is_relative_to`). 403 sinon.
     Même garde-fou que `/open-file`.
   - 412 si profil/cv_output_dir absent ou PDF introuvable, 504 timeout
     30s, 500 inattendu.
3. **Auto-trigger côté service worker** : après `setState({cvState:'done',
   cvResult: data})` dans `runTailorCv`, fetch `/ats-lint` avec
   `data.saved_path` + l'offer. Au retour, on patch `cvResult.ats`. Si
   le lint plante (backend off, etc.), warn console — le badge ne s'affiche
   simplement pas, le CV reste utilisable.
4. **Popup** : `AtsBadge` rendu sous le filename du CV. Pill colorée selon
   le score (vert ≥ 70, ambre 45-69, rouge < 45). Bouton "voir le détail"
   déploie un panel avec les suggestions actionnables + la liste des
   checks (✓/✕ + label FR + détail). Pas d'emoji.

### 2.quater. `qa_bank` — banque de réponses canoniques

Champ optionnel `qa_bank` dans `user_profile.json` : mapping
`{clé canonique: réponse de référence}` pour les questions récurrentes
des formulaires (availability, salary_expectations, notice_period,
visa_sponsorship, relocation, motivation_template, why_us_template).

Le prompt de `form_filler._SYSTEM` instruit Gemini à **adapter** la
réponse de la banque au champ (interpole `{company}`/`{title}`, ajuste
la phrasing au type du champ — texte court vs textarea long) au lieu
de la régénérer à froid. Apparariement flou autorisé ("Quand seriez-vous
disponible ?" → `availability`).

Aucun changement de payload : la `qa_bank` est sérialisée comme le reste
du profil. Rétrocompat : un profil sans `qa_bank` continue à marcher
comme avant.

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

Note : `/fill-form` est **découplé** de `/scrape-job`. L'idle screen
expose deux CTAs — "Analyser l'offre" et "Remplir le formulaire" — pour
que l'utilisateur puisse remplir directement sur une page de candidature
sans devoir scraper l'offre au préalable.

## API Backend — Endpoints

```
GET  /health         → {status, llm_available, form_filler_available,
                        cv_tailor_available, cover_letter_available,
                        match_scorer_available, ats_lint_available}

POST /scrape-job     body: {job_url, job_html}
                     → champs offre + {llm_used, llm_error?, from_cache,
                                       application_id, seen_before,
                                       application_status}

POST /match-score    body: {offer}
                     → {score, matched_skills, missing_skills, rationale,
                        llm_used, application_id?}

POST /tailor-cv      body: {offer, match?: {matched_skills, missing_skills}}
                     → {saved_path, saved_docx_path, filename, folder,
                        edited_count, editable_count}

POST /cover-letter   body: {offer, match?: {matched_skills, missing_skills}}
                     → {text, saved_path, saved_docx_path, filename, folder}

POST /ats-lint       body: {pdf_path, offer}
                     → {ats_score, checks[], suggestions[],
                        matched_skills, missing_skills, page_count,
                        text_length}. Auto-déclenché par le service worker
                        après /tailor-cv réussit.

POST /fill-form      body: {form_schema, context?}
                     → {values: {field_id: value}, cv_base64?}

POST /open-file      body: {path}  — ouvre PDF/DOCX dans le lecteur OS
                     (os.startfile / open / xdg-open). Validation :
                     extension .pdf|.docx ET path sous cv_output_dir.

GET  /applications              ?status=&company=&since=&until=
                                consommé par la page tracker
GET  /applications/{id}         → row ou 404
PATCH /applications/{id}        body: {status?, notes?}
                                422 si status invalide, 404 si absent.
                                Appelé par : (a) auto après fill-form OK
                                (status='applied'), (b) dropdown popup,
                                (c) dropdown/notes tracker
```

Codes d'erreur :
- `403` validation chemin (open-file hors `cv_output_dir` ou extension non autorisée)
- `404` ressource introuvable (application_id, fichier)
- `412` ressource absente (profil, CV base, `cv_output_dir` non configuré)
- `422` validation (HTML/URL vide, offre incomplète, status invalide)
- `502` LLM en erreur (clé invalide, 429, JSON mal formé)
- `504` timeout (15s scraping, 30s LLM, 60s tailoring)
- `500` erreur inattendue

## Extension — service worker + popup

- **Manifest V3**, `host_permissions: ["<all_urls>"]`,
  `content_scripts.matches: ["<all_urls>"]`, `permissions: ["storage",
  "activeTab", "scripting"]`.
- **Background service worker** (`background.ts`) : execute scrape/tailor/
  fill via `fetch` → backend `localhost:8000` et écrit dans
  `chrome.storage.local`. Survit à la fermeture du popup. Messages écoutés :
  `START_ANALYZE`, `START_TAILOR_CV`, `START_FILL_FORM`,
  `PATCH_APPLICATION` (status/notes update), `OPEN_TRACKER` (ouvre
  l'onglet tracker), `RESET_STATE`.
- **Popup** : hydrate `chrome.storage.local` à l'ouverture + écoute
  `chrome.storage.onChanged`. 7 états + cvState parallèle. Idle screen
  avec 2 CTAs (Analyser / Remplir le formulaire). Boutons : Analyser,
  Adapter le CV (+ ↻ regénérer), Postuler, ↺ reset (TopBar), ▤ Suivi
  (TopBar — ouvre le tracker). Raccourci `Ctrl ↵` / `⌘ ↵` pour "Postuler".
- **Layout popup** : `html` + `body` 400×600 fixes + `overflow:hidden`,
  panel 100%/100%, body `flex: 1 1 0` + `min-height: 0` pour permettre le
  scroll vertical à l'intérieur sans tronquer le contenu (bug récurrent).
- **Ouverture PDF cross-browser** : `handleOpenCv` POSTe vers
  `/open-file` (pas `chrome.tabs.create({url:'file://...'})`, bloqué par
  Chrome et Firefox sans flag manuel).
- **Communication** : popup → `chrome.runtime.sendMessage` → service worker →
  `chrome.tabs.sendMessage` → content (`CAPTURE_JOB_HTML`/`DETECT_FORM`/
  `FILL_FORM`) → fetch → backend.
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
- Caveat Firefox : `chrome.tabs.create({url: "file:///..."})` est bloqué
  par défaut. Contourné par `/open-file` côté backend.

## Règles importantes

- **Logger** chaque appel API dans `backend/logs/{date}.log` (INFO + erreurs).
- **CORS** : FastAPI accepte `*`, `allow_credentials=False` (requis avec
  wildcard).
- **Timeouts** : 15s scrape, 30s LLM extract / fill-form, 60s tailor-cv.
- **Clé API** : `GEMINI_API_KEY` dans `.env`, jamais hardcodée, jamais
  loggée. Créer sur https://aistudio.google.com/apikey.
- **uvicorn `--reload`** ne watch pas `.env` : changer la clé impose un
  redémarrage complet (`dev.ps1` tue les orphelins sur 8000/5173).
- **Tests** : un fichier par agent dans `tests/`, mocks Gemini +
  `_convert_docx_to_pdf` (le pipeline DOCX → PDF passe par Word COM,
  pas testable sans Word installé). `test_store.py` monkeypatche
  `DB_PATH` vers `tmp_path` pour l'isolation.
- **SQLite** : `sqlite3` stdlib only, pas de SQLAlchemy/Alembic. Schéma
  figé via `CREATE TABLE IF NOT EXISTS`, idempotent.
- **Encoding** : `dev.ps1` en ASCII pur (Windows PowerShell lit CP-1252 par
  défaut).
- **Profil utilisateur** : `backend/data/user_profile.json` est gitignoré.
  Modèle : `user_profile.example.json`. La DB `applications.db` est
  aussi gitignorée.

## Variables d'environnement (`.env`)

- `GEMINI_API_KEY` — clé Google Gemini (vide → toutes les étapes LLM
  désactivées, dégradation propre)
- `GEMINI_MODEL` — défaut `gemini-2.5-flash` (`gemini-2.0-flash` a été
  démoté hors free tier)
- `DB_PATH` — chemin custom pour `applications.db` (défaut
  `backend/data/applications.db`)
- `BACKEND_PORT`, `LOG_LEVEL`

## Format des commits

`feat(scope): description`
Ex : `feat(store): SQLite tracking + scrape cache`,
`feat(cv): section-aware tailoring (SUMMARY + Relevant coursework only)`,
`fix(popup): persist state across close via chrome.storage.local`.

## Definition of Done

- [ ] Code fonctionnel et testé manuellement sur ≥1 site cible
- [ ] Tests unitaires (pytest) couvrent JSON-LD, meta, fallback, erreurs,
      LLM mocké, slug, filename, section detection, banned-cliché audit,
      hash determinism, dédup, cache miss/hit
- [ ] Docstrings sur les fonctions publiques
- [ ] Logs sur les étapes clés
- [ ] `npx tsc --noEmit` propre (extension)
- [ ] `pytest` vert (96 tests actuellement)

## En fin de session — mettre à jour REPORT.md

Avec : ce qui est implémenté, ce qui reste, décisions d'archi, blocages.
Bannière "Superseded" sur les entrées rendues obsolètes par un pivot.

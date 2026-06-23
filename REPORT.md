# REPORT.md — Job Apply Agent MVP

## 2026-06-23 — Score de pertinence offre/profil (match_scorer)

### Implémenté

- **`backend/agents/match_scorer.py`** — nouvel agent. Cascade :
  - **LLM path** (`is_available()` → clé Gemini présente) : prompt strict
    qui exige un JSON `{score 0-100, matched_skills, missing_skills,
    rationale ≤ 280 chars}`, response_mime_type `application/json`,
    temperature 0. Parsing tolérant aux fences ```` ```json ... ``` ````.
    Clamp du score dans `[0, 100]`. Garde-fou no-invention dans le prompt
    (matched_skills ne contient que ce qui est explicitement dans le
    profil).
  - **Fallback déterministe** : overlap normalisé sur les skills (NFKD +
    ASCII + lowercase + collapse). Choix overlap vs Jaccard parce qu'un
    candidat avec PLUS de skills que ce que l'offre demande ne doit pas
    être pénalisé (Jaccard ferait baisser le score). Score = matched /
    total_offer * 100. Dédup au passage. Rationale auto-générée.
  - **Jamais d'exception côté appelant** : LLM en erreur (JSON KO,
    réseau, rate limit, structure inattendue) → log puis fallback. Garantit
    une jauge toujours rendue côté extension.
- **`POST /match-score`** — timeout 30 s, persistance opportuniste :
  si `compute_job_hash(title, company, location)` matche une ligne
  `applications` existante, on écrit `match_score` via le nouveau helper
  **`store.set_match_score(application_id, score)`** (UPDATE direct, ne
  crée pas la ligne — c'est le rôle exclusif de `/scrape-job`).
- **`/tailor-cv` extension** : body accepte un champ optionnel `match:
  {matched_skills, missing_skills}`. `cv_tailor.tailor_cv(offer,
  match=None)` injecte un bloc `--- MATCH ---` dans le prompt Gemini avec
  `matched_skills_emphasize_truthfully` (à valoriser quand le profil les
  supporte déjà) et `missing_skills_do_not_claim_present` (à ne PAS
  inventer dans les bullets). Rétrocompat strict si `match` est absent.
- **Extension** : 
  - Le service worker fetch `/match-score` **automatiquement** dès qu'un
    `/scrape-job` termine en succès. Le résultat est attaché à
    `result.match` dans `chrome.storage.local` → la popup le rend dès
    l'arrivée via le subscriber `onChanged`.
  - **`MatchCard`** dans Popup.tsx (état `ready`) : carte sobre Atelier
    avec score grand (vert ≥70, ambre 45-69, rouge <45), barre de
    progression, rationale, chips des missing_skills (12 max + "+N"
    overflow). Mode "gemini" / "hors-ligne" affiché en petit pour
    transparence. Placeholder "Calcul du score…" tant que `result.match`
    est undefined (entre la fin de scrape et le retour match-score).
  - `runTailorCv` forward `result.match` vers `/tailor-cv` si présent.
- **Tests** : `tests/test_match_scorer.py` — 33 tests, suite totale
  **129 verts**. Couvre :
  - `is_available` (clé set/unset)
  - `_clamp_score` (clamp haut/bas, cast string/float/None/abc)
  - `_normalize` (NFKD + case + whitespace)
  - `_extract_profile_skills` (list, dict par catégorie, manquant, None)
  - Fallback : partial / full / zero overlap, no offer skills, normalisation,
    dédup, output shape
  - LLM path mocké : JSON propre, fences markdown, clamp >100 / <0 / non-numérique,
    rationale tronquée, keys manquantes par défaut []
  - Cascade : JSON invalide → fallback, RuntimeError → fallback, JSON
    array (non-dict) → fallback. Output shape garantie sur les deux chemins.

### Décisions

- **Le score ne fail jamais l'analyse principale** : auto-trigger derrière
  un try/catch silencieux (warn console). La popup montre l'offre même si
  match-score plante. Cohérent avec la philosophie "dégradation propre"
  utilisée partout ailleurs.
- **Overlap > Jaccard** : un candidat avec des skills extras n'est pas
  pénalisé. Pour un score "% des compétences requises que je possède", ça
  parle plus que Jaccard qui descend mécaniquement avec les extras.
- **Pas de création d'application dans `/match-score`** : on écrit
  `match_score` seulement si une application existe déjà (créée par
  `/scrape-job`). Évite les fantômes en DB quand on requête sans avoir
  scrapé.
- **Forward du match dans `/tailor-cv` côté service worker, pas côté
  popup** : la popup ne sait pas que le tailor-cv profite du match, et
  c'est très bien — le service worker est l'orchestrateur, il a déjà tout
  l'état en `chrome.storage.local`.
- **Placeholder pendant calcul** : `match === undefined` ≠ `match === null`.
  Undefined = pas encore arrivé (cas normal). Pas de différentiation
  null/undefined dans le rendu (le warn console suffit en cas d'échec).

### Reste à faire / open

- **Re-trigger manuel** : si l'utilisateur veut recalculer un score
  (changement de profil entre deux essais), il faut reset l'extension et
  refaire un Analyser. Pas de bouton "Recalculer le score" pour l'instant.
- **Tracker** : pas encore de colonne `match_score` affichée dans la
  page tracker. La donnée est persistée en DB mais le rendu ne l'utilise
  pas encore. À faire si on veut trier par score dans le tracker.
- **Threshold "skip cette offre"** : pas de notif/raccourci visuel
  spécial pour les scores très bas (<30). Le score parle déjà de
  lui-même côté UX ; à voir si on ajoute un bandeau "match faible".

## 2026-06-23 — Suivi des candidatures + transitions de statut

### Implémenté

- **Auto-PATCH après fill-form succès** : le service worker appelle
  `PATCH /applications/{application_id}` avec `status='applied'` si
  `application_id` est présent dans `result` (donc si l'utilisateur a
  scrapé l'offre avant de remplir). Sans application_id (cas "remplir
  sans analyse"), skip silencieux — l'utilisateur marquera manuellement.
- **Dropdown statut dans le popup** : remplace l'ancien badge "Déjà vu"
  qui n'était que lecture. Le select est rendu dès que
  `application_id` existe, avec optimistic update + PATCH via le service
  worker. Le badge se met à jour live (onChanged → reflectStatusInStorage).
- **Tracker — page plein écran** (`src/tracker/index.html`) ouverte via
  bouton ▤ Suivi dans la TopBar du popup (`chrome.runtime.getURL` +
  `chrome.tabs.create`). UX :
  - Header + bouton ↻ Recharger
  - 6 stat cards : total, déjà postulé, relancées, entretiens, réponses +/-
  - Filtre : chips de statuts (Tous + un par status) + recherche texte
    (matche company/title/location en sous-chaîne)
  - Liste groupée par société (collapsibles via header)
  - Chaque ligne : titre, location, contrat, "vue il y a Xj", dropdown
    statut éditable (avec teinte selon statut), textarea notes (debounce
    700 ms avant PATCH), liens ↗ offre + 📄 CV (passe par `/open-file`)
- **Module partagé `extension/src/shared/status.ts`** : APPLICATION_STATUSES
  + STATUS_LABELS + isProgressedStatus, importés par popup et tracker
  (source of truth synchronisée avec `backend/store.VALID_STATUSES`).
- **Build** : `tracker/index.html` ajouté à `rollupOptions.input` dans
  `vite.config.ts` (CRXJS ne traite pas les HTML dans
  `web_accessible_resources` — sans cette ligne, le `<script
  src="./index.tsx">` était copié brut et le navigateur tentait de
  charger du TSX). Listé dans `manifest.web_accessible_resources` pour
  que `chrome.runtime.getURL(...)` résolve. Firefox build OK.
- **`@types/node`** ajouté en devDep pour que `tsc --noEmit` accepte les
  imports `node:path`/`node:url` du vite.config.ts.

### Décisions

- **`store.set_match_score` vs `update_application`** : helper dédié pour
  éviter qu'un PATCH manuel mal formé n'écrase un score calculé. Plus
  défensif que d'ajouter `match_score` à `update_application`.
- **Tracker autonome côté frontend** : pas de couche
  `chrome.storage.local`, pas d'auto-refresh WebSocket. Le bouton ↻ suffit.
  Le tracker est conçu pour être ouvert ponctuellement, pas en permanence.
- **Auto-PATCH skippé si pas d'application_id** : préserve la propreté
  de la DB. Mieux que créer une ligne avec des données partielles
  inférées du formulaire.

## 2026-06-23 — Tailoring strictly scoped + filename convention propre

### Implémenté

- **Filename convention** : `0_CV_Firstname_Lastname_JobTitle.pdf`
  (vs ancien `0_cv_firstname_lastname_jobtitle_company.pdf`). Changements :
  CV en majuscules, prénom/nom Title-case, titre canonicalisé (drop
  parenthétiques, suffixes après tiret, marqueurs F/H), nom d'entreprise
  retiré (déjà dans le dossier parent). Exemple :
  "Deep Learning Algorithm Graduate (TikTok Search Ranking) - 2026 Start
  (BS/MS)" -> `0_CV_Alexandru_Nitescu_Deep_Learning_Algorithm_Graduate.pdf`.
- **`_canonical_job_title`** + **`_slug_title`** : nouveaux helpers.
  `_slug_title` préserve les acronymes ALL-CAPS courts (BS, MS, AI, ML,
  NLP) au lieu de les rabattre en Title case (Bs/Ms/...).
- **Tailoring section-aware** : la liste d'éditables n'est plus une
  heuristique liberale sur tous les bullets ; elle est strictement scopée
  via `_collect_editable_in_sections(doc)` à :
  - tout paragraphe substantiel sous **SUMMARY**
  - UNIQUEMENT la ligne **'Relevant coursework...'** sous **EDUCATION**
  Tout le reste (header, contact, EXPERIENCE bullets, PROJECTS, SKILLS,
  LANGUAGES, CERTIFICATIONS) reste gelé avec formatting d'origine
  préservé.
- **`_is_section_header`** + **`_normalize_section`** : détecte les
  en-têtes (all-caps, ≤50 chars, tabs tolérées car Word ajoute des tabs
  de padding) et les mappe vers un tag canonique via `_SECTION_KEYWORDS`
  (SUMMARY accepte SUMMARY/PROFILE/OBJECTIVE/ABOUT, EDUCATION accepte
  EDUCATION/ACADEMIC/DIPLOMA, etc.).
- **Comportement empty editable** : si le DOCX ne contient ni SUMMARY ni
  Relevant coursework, on ne crashe plus avec ValueError — on convertit
  le DOCX tel quel en PDF. L'utilisateur récupère son CV original dans
  le dossier de l'offre, sans tailoring.
- **Tests** : test_cv_tailor.py refondu — 36 tests (vs 25 avant) :
  `_slug_title` (Title case, acronymes), `_canonical_job_title` (parens,
  dashes en/em, gender markers, slash AI/ML préservé),
  `make_filename` (nouvelle convention, F/H stripping, fallback),
  `_is_section_header`, `_normalize_section`,
  `_collect_editable_in_sections` (picks SUMMARY content, picks
  Relevant coursework, ignores EXPERIENCE bullets, ignores EDUCATION
  projects, empty fallback), orchestration end-to-end avec rogue idx
  filtré. Sur le vrai CV.docx de l'user : 43 paragraphes -> 2 éditables
  (SUMMARY content + Relevant coursework). Suite totale : 96 verts.

### Décisions

- **Scope strict** plutôt qu'heuristique généreuse : la session
  précédente tailorait n'importe quel paragraphe substantiel, ce qui
  cassait le formatting du CV utilisateur (texte mis en gras par
  erreur, structure perturbée). Le retour explicite : ne JAMAIS toucher
  aux bullets EXPERIENCE ou aux descriptions de projets — un CV tailoré
  doit rester visuellement IDENTIQUE au CV de base, sauf pour les
  passages où le tailoring a vraiment du sens (positionnement narratif
  = SUMMARY, mots-clés académiques = coursework).
- **Filename sans company** : redondant avec le dossier parent
  `{cv_output_dir}/{Company}/`. Économise des caractères dans les noms
  trop longs et reflète la convention demandée (CV_Prénom_Nom_Titre).
- **Title case explicite** pour le prénom/nom plutôt que `_slug(allow_caps=True)`
  qui se contente de préserver la casse d'entrée : garantit un rendu
  uniforme même si le profil contient des noms en lowercase.
- **Acronymes ≤4 chars préservés** : BS_MS reste BS_MS, pas Bs_Ms. La
  longueur 4 couvre les acronymes courants (AI, ML, BS, MS, NLP, CDI,
  GPT) sans risquer de garder des mots normaux comme "Time" en TIME.

### Blocages

Aucun. Les anciens tests qui dépendaient de `_is_editable` (renommé en
`_is_substantive`) et de l'orchestrator avec fixture sans SUMMARY ont
été réécrits autour de la nouvelle fixture `_make_user_like_docx` qui
mime un CV réaliste (header + SUMMARY + EXPERIENCE + EDUCATION).

---

## 2026-06-23 — SQLite tracking + cache scrape-job

### Implémenté

- **`backend/store.py`** — module DB autonome, sqlite3 stdlib (pas
  d'ORM, pas d'Alembic). `init_db()` idempotent via CREATE TABLE IF
  NOT EXISTS, chemin configurable via `DB_PATH` (.env), défaut
  `backend/data/applications.db`. Connexion open-close par appel via
  un contextmanager, `sqlite3.Row` row_factory pour l'accès par nom.
  Schéma deux tables :
  - `applications(id, job_url, job_hash UNIQUE, company, title,
    location, contract_type, status, match_score, cv_path,
    cover_letter_path, notes, created_at, updated_at)` + index sur
    status, company, created_at.
  - `scrapes(job_hash PK, essentials_json, created_at)` — cache des
    résultats de /scrape-job pour économiser des appels Gemini.
- **`compute_job_hash`** — sha256 d'un canonical normalisé
  `title|company|location` avec NFKD + drop accents + collapse espaces
  + lowercase. Garantit que `L'Oréal` et `L'Oreal` dédupliquent
  correctement.
- **`upsert_application`** — INSERT si nouveau (status='seen'),
  UPDATE par COALESCE des champs scraping sinon. Le status manuel
  (applied, interview, etc.) est PRÉSERVÉ entre re-scrapes — un
  re-scrape ne repasse pas une application 'applied' à 'seen'.
  Retourne `(id, was_new)` pour le badge popup.
- **`/scrape-job` modifié** : calcule le hash post-scraping structurel,
  check du cache → court-circuit Gemini si hit (flag `from_cache: true`),
  sinon Gemini puis save_scrape_cache. Dans tous les cas upsert
  l'application et renvoie `{application_id, seen_before,
  application_status}` pour le badge.
- **`/tailor-cv` modifié** : compute_job_hash sur l'offre, find/create
  l'application, écrit `cv_path = saved_path` du PDF tailoré. Le user
  peut tracer quel CV a été généré pour quelle candidature.
- **Nouveaux endpoints** :
  - `GET /applications?status=&company=&since=&until=` — filtres
    optionnels (company en LIKE %x%, dates ISO 8601), tri created_at
    DESC.
  - `GET /applications/{id}` — 404 si absent.
  - `PATCH /applications/{id}` — status et/ou notes. 422 si status
    hors VALID_STATUSES, 404 si id absent.
- **Lifespan FastAPI** : `store.init_db()` exécuté au startup via
  `@asynccontextmanager` — la DB et les tables sont garanties prêtes
  avant le premier handler.
- **Popup.tsx** : `OfferResult` étendu avec `application_id`,
  `seen_before`, `application_status`, `from_cache`. Badge sobre
  affiché à droite du tag LLM dans l'état ready :
  - `seen_before && status === 'seen'` → badge muted "Déjà vu"
  - `seen_before && status !== 'seen'` → badge accent vert avec
    label (Déjà postulé / Relancée / Entretien / Réponse positive /
    Réponse négative).
- **Tests** : `tests/test_store.py` — 25 tests couvrant init
  idempotent, hash déterministe / normalisation accents / dédup,
  upsert création/update/préservation status, COALESCE qui n'écrase
  pas avec None, get_application(_by_hash), list filtres status /
  company LIKE / date range, update PATCH partiel, validation status,
  noop sans patch, cache miss/hit/upsert/unicode payload. Suite
  totale : 80 verts.
- **`.gitignore`** : `backend/data/*.db` + `*.db-journal` (la DB
  applications.db ne doit jamais être commit).

### Décisions

- **Hash sur les champs structurels (pré-LLM)** plutôt que post-LLM :
  garantit la convergence cache lookup ↔ cache write sur les visites
  suivantes (sinon le LLM pourrait modifier title/company et générer
  un hash différent → cache miss systématique).
- **COALESCE en UPDATE** : on ne wipe pas une valeur connue avec NULL
  si le re-scrape rate un champ (e.g. l'utilisateur revoit la même
  offre depuis une page condensée qui n'expose plus la location).
- **`status` validé en Python**, pas en CHECK constraint SQL :
  permet d'ajouter un statut au tuple `VALID_STATUSES` sans migration
  de schéma.
- **Open-close par requête** : `_conn()` ouvre + ferme à chaque
  appel via contextmanager. sqlite3 supporte parfaitement ce pattern
  pour des charges légères, et ça évite tous les soucis de partage
  de connexion entre threads de l'executor uvicorn.
- **Badge sobre, pas de vue lourde** (per roadmap) : un simple tag
  Atelier dans l'état ready suffit à signaler la réoccurrence et
  l'avancement. Une vue dédiée "kanban des candidatures" pourra
  arriver plus tard si le besoin est confirmé.

### Blocages

Aucun. Les anciens tests cv_tailor (déjà adaptés au pipeline
DOCX-template lors d'une session précédente) restent intacts. Aucun
warning DeprecationWarning ajouté.

---

## 2026-06-09 — Tailored summary in CV pipeline

> **⚠ Superseded — historique uniquement.** La passe SUMMARY séparée
> a été retirée lors du refactor vers le pipeline DOCX-template
> (cv_tailor.py édite désormais en place le DOCX source de l'user au
> lieu de reconstruire un Markdown). Le summary, s'il existe dans le
> DOCX, est tailoré comme n'importe quel autre paragraphe éditable.

### Implémenté

- **`backend/agents/cv_tailor.py`** — pipeline élargi : une passe Gemini
  dédiée génère un SUMMARY de 2-3 phrases avant le pass principal de
  rédaction du CV. Le SUMMARY est injecté en tête du Markdown (juste avant
  la première section `## `, ou en fin de doc si aucune) via le helper
  `_inject_summary`. La passe principale ne produit plus de section
  "Summary" elle-même (prompt mis à jour : "Do NOT add a Summary, Profile
  or Objective section").
- **Garde-fous summary** dans le system prompt dédié :
  - chaque phrase doit s'appuyer sur un fait concret du profil ou de la
    base CV (techno, métrique, projet, école, année)
  - 2-4 keywords du job offer doivent être mirrorés, uniquement si ils
    sont réellement supportés par le profil
  - clichés interdits centralisés dans la constante `BANNED_CLICHES`
    ("passionate", "team player", "fast learner", "results-oriented", …)
  - pas d'ambitions disqualifiantes (manager sur un poste IC, recherche
    sur un poste applicatif)
- **`_clean_summary`** : strip quotes (`"`, `'`, courbes), fences `\`\`\`…\`\`\``,
  marqueurs heading (`#+ `), préfixes inline `Summary: …` ET premières
  lignes-mot-clé `Summary\n…` (le modèle insiste parfois pour ajouter
  un titre).
- **Dégradation silencieuse** : si la passe SUMMARY échoue (clé API
  absente, 429, output trop court, exception), `generate_summary`
  retourne `None` sans lever, le CV est généré sans section SUMMARY,
  le flag `summary_used: false` est exposé dans la réponse de
  `/tailor-cv`. Aucun blocage du pipeline principal.
- **Toggle utilisateur** : `profile.include_summary` (bool, défaut `true`).
  À `false`, la passe SUMMARY n'est même pas tentée — un seul appel
  Gemini.
- **`backend/data/user_profile.example.json`** — ajout du champ
  `include_summary` documenté dans le `_comment`.
- **`tests/test_cv_tailor.py`** — 14 tests ajoutés (55 au total) :
  `generate_summary` happy path, strip quotes/fences/headings, empty,
  too-short, LLM exception, short-circuit sans clé API ; `_inject_summary`
  ordering vs premier `##`, append-when-no-h2, noop sur summary vide ;
  intégration `tailor_cv` avec `include_summary=true|false` et avec
  l'erreur LLM sur la 1ère passe ; audit "aucun cliché interdit dans
  l'output mocké" + sanity check sur la constante elle-même.

### Décisions

- **Deux passes plutôt qu'une** : passer le SUMMARY dans le prompt
  principal a été écarté — l'isoler permet un prompt dédié avec règles
  strictes (clichés, garde-fous d'ambition) sans alourdir le prompt
  principal, et la passe principale ne consomme pas de tokens à écrire
  un Summary qui sera de toute façon réécrit.
- **Injection texte plutôt que prompt-feeding** : `_inject_summary`
  fait une simple insertion Markdown plutôt que de demander au modèle
  principal d'inclure un résumé déjà écrit. C'est plus déterministe
  (pas de risque de paraphrase) et plus simple à tester.
- **Pas d'enforcement runtime des clichés** : les `BANNED_CLICHES` sont
  une contrainte du prompt, pas un filtre côté code. Le test
  documente l'invariant sans pénaliser un faux positif (e.g. "team"
  dans un autre contexte).
- **Logging discret** : `cv_tailor: summary=ok|skipped` en INFO, pas
  d'alarme sur le `skipped` (c'est une dégradation acceptée).

### Blocages

- Aucun. Tests verts du premier coup hors une réécriture mineure
  de `_clean_summary` pour gérer le cas `## Summary\n{texte}`
  (mot-clé seul sur sa ligne, sans `:` ou `-` suivants), repéré par
  les tests `strips_quotes_and_fences`.

---

## Date
2026-02-22

> **⚠ Superseded — historique uniquement.** Cette entrée décrit une
> architecture antérieure basée sur Anthropic Claude avec les agents
> `cv_adapter.py`, `job_analyzer.py`, `orchestrator.py` et l'endpoint
> `/analyze-and-adapt`. Depuis le pivot vers Scrapling + Gemini (commit
> `416f219`, février 2026), ces fichiers et endpoints n'existent plus.
> Conservée pour traçabilité de session.

## Ce qui est implémenté

### Backend (FastAPI)

- **`backend/main.py`** : application FastAPI avec 3 endpoints (`GET /health`, `GET /cv`, `POST /analyze-and-adapt`), CORS configuré (`allow_origins=["*"]`, `allow_credentials=False`), logging vers `backend/logs/{date}.log`, `load_dotenv()` appelé en tête de fichier avant les imports agents
- **`backend/agents/job_analyzer.py`** : sous-agent d'analyse d'offre, utilise `claude-haiku-4-5-20251001`, extrait 6 champs structurés (title, company, required_skills, experience_level, culture_values, main_missions), gestion des blocs markdown dans les réponses JSON, timeout 30s
- **`backend/agents/cv_adapter.py`** : sous-agent d'adaptation du CV, utilise `claude-haiku-4-5-20251001`, max_tokens=4096, détection des compétences inventées via `_warn_if_invented_skills`, règles strictes dans le system prompt (ne pas inventer, ne pas modifier les faits), timeout 30s
- **`backend/agents/orchestrator.py`** : pipeline asynchrone avec `asyncio.wait_for` (timeout 60s), exécute les agents synchrones via `run_in_executor`, étape de validation par `claude-opus-4-6-20251101`, chargement de `cv_base.json`
- **`backend/data/cv_base.json`** : fichier de référence, non modifié
- **`backend/logs/`** : répertoire créé automatiquement au démarrage

### Extension Chrome

- **`extension/public/manifest.json`** : Manifest V3, 5 sites supportés (LinkedIn, HelloWork, JobTeaser, Indeed FR, Welcome to the Jungle), content script déclaré, service worker background, permissions `activeTab` et `scripting`
- **`extension/src/popup/Popup.tsx`** : interface React avec 4 états (idle, loading, result, error), extraction via message au content script, appel fetch vers `localhost:8000/analyze-and-adapt`, affichage du `match_score` en pourcentage, résumé adapté, bouton "Copier le CV" (clipboard API), gestion d'erreur avec message utilisateur
- **`extension/src/content/scraper.ts`** : content script qui extrait le texte visible, supprime nav/footer/header/scripts/styles, normalise les espaces, limite à 8 000 caractères, écoute le message `EXTRACT_JOB_TEXT`
- **`extension/src/background.ts`** : service worker minimal (log installation)
- **`extension/vite.config.ts`** : build Vite avec `@vitejs/plugin-react` et `@crxjs/vite-plugin`
- **`extension/package.json`** : React 18, TypeScript 5.5, Vite 5, CRXJS beta

### Tests

- **18 tests unitaires**, tous passants (1.14s d'exécution)
- `tests/test_job_analyzer.py` : 6 tests — structure retournée, modèle correct, timeout 30s, gestion markdown, JSON invalide, texte court
- `tests/test_cv_adapter.py` : 7 tests — structure + match_score, absence de compétences inventées, warning sur compétence inventée, faits non modifiés, modèle correct, max_tokens suffisant, JSON invalide
- `tests/test_orchestrator.py` : 5 tests — clés de retour, ValueError sur job_text vide, ValueError sur whitespace, ordre d'appel analyze→adapt, propagation d'erreur sous-agent
- Configuration `pytest.ini` : `asyncio_mode = auto`, `testpaths = tests`

## Ce qui reste à faire (hors scope MVP)

- Icones réelles pour l'extension (actuellement référencées dans le manifest mais fichiers icon16/48/128.png absents du répertoire `public/`)
- Tests d'intégration end-to-end (extension → backend réel)
- Déploiement backend (actuellement localhost uniquement)
- Support d'autres sites d'offres d'emploi (pole-emploi.fr, apec.fr, etc.)
- Interface de personnalisation du CV de base depuis l'extension
- Cache des analyses pour éviter les appels redondants sur la même offre
- Rate limiting sur le backend
- Mode hors-ligne avec résultats mis en cache
- Internationalisation de l'interface popup (actuellement en français uniquement)
- Publication sur le Chrome Web Store

## Décisions d'architecture

- **Agents synchrones dans un pipeline asynchrone** : `job_analyzer` et `cv_adapter` utilisent le SDK Anthropic synchrone. L'orchestrateur les enveloppe dans `asyncio.run_in_executor` pour ne pas bloquer l'event loop FastAPI, ce qui permet d'avoir un timeout global via `asyncio.wait_for`.
- **Modèles distincts par rôle** : Haiku (moins coûteux) pour les tâches bien définies (extraction, adaptation), Opus (plus puissant) pour la validation de cohérence — optimisation coût/qualité.
- **Validation par LLM** : l'orchestrateur utilise Claude Opus comme juge de cohérence entre `job_data` et `adapted_cv`. Si la validation échoue, le pipeline retourne quand même un résultat avec un warning log (pas d'erreur fatale), ce qui évite de bloquer l'utilisateur pour un faux positif.
- **Pas de base de données** : `cv_base.json` est la source de vérité unique, chargé à chaque appel pour éviter un état global mutable.
- **CORS wildcard avec credentials=False** : adapté pour une extension Chrome en développement local. `allow_credentials=False` est obligatoire avec `allow_origins=["*"]` (restriction FastAPI/CORS spec).
- **Styles inline dans Popup.tsx** : choix délibéré pour éviter les dépendances CSS externes et simplifier le build CRXJS.
- **Limite 8000 caractères** dans le scraper : compromis entre exhaustivité de l'offre et coût des tokens Haiku.

## Blocages rencontrés

- **Import conditionnel dans main.py** : `from backend.agents.orchestrator import run_pipeline` doit être placé après `load_dotenv()` pour que la variable `ANTHROPIC_API_KEY` soit disponible à l'initialisation du client Anthropic dans les modules agents. Le commentaire `# noqa: E402` indique que cet import est volontairement hors de l'ordre standard.
- **Tests asyncio** : `pytest-asyncio==0.24.0` requiert `asyncio_mode = auto` dans `pytest.ini` pour décorer les tests avec `@pytest.mark.asyncio` sans avoir à configurer manuellement la boucle d'événements.
- **Markdown dans les réponses LLM** : les modèles Haiku enveloppent parfois leur JSON dans des fences ` ```json ``` `. La fonction `_parse_json_response` (présente dans les deux agents) gère ce cas via des regex de nettoyage.

## Vérifications finales

| Contrainte CLAUDE.md | Statut | Détail |
|---|---|---|
| CORS `allow_credentials=False` + `allow_origins=["*"]` | OK | Combinaison valide (credentials=False est requis avec wildcard) |
| Modèle Haiku dans job_analyzer | OK | `claude-haiku-4-5-20251001` ligne 59 |
| Modèle Haiku dans cv_adapter | OK | `claude-haiku-4-5-20251001` ligne 90 |
| Modèle Opus dans orchestrator | OK | `claude-opus-4-6-20251101` dans `ORCHESTRATOR_MODEL` |
| Aucune clé API hardcodée | OK | `Anthropic()` lit `ANTHROPIC_API_KEY` depuis l'environnement |
| `load_dotenv()` dans main.py | OK | Ligne 12, avant les imports agents |
| Logs dans `backend/logs/{date}.log` | OK | `FileHandler` avec `date.today()` |
| `backend/data/cv_base.json` non modifié | OK | Fichier intact |
| `.env` non modifié | OK | Fichier intact |
| Timeout 30s par sous-agent | OK | `timeout=30.0` dans job_analyzer et cv_adapter |
| Timeout 60s pipeline | OK | `asyncio.wait_for(_run(), timeout=60.0)` |
| Tests unitaires par agent | OK | 18 tests, 18 passants |
| Docstrings sur fonctions publiques | OK | Présentes sur toutes les fonctions publiques backend |
| Variables d'environnement via python-dotenv | OK | `.env` + `load_dotenv()` |

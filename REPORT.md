# REPORT.md — Job Apply Agent MVP

## 2026-06-09 — Tailored summary in CV pipeline

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

# CLAUDE.md — Job Apply Agent (MVP)

## Objectif MVP

Extension Chrome React/TypeScript qui s'active sur une offre d'emploi,
extrait le contenu de la page, l'envoie à un backend FastAPI qui :
1. Analyse et structure l'offre via un sous-agent
2. Adapte un CV JSON de base à cette offre via un sous-agent
3. Retourne le CV adapté à afficher dans l'extension

## Architecture
```
job-apply-agent/
├── extension/          Chrome Extension React + TypeScript (Vite + CRXJS)
├── backend/            FastAPI Python async
│   ├── agents/         Logique des agents Claude
│   └── data/           cv_base.json (source de vérité)
├── tests/
└── REPORT.md
```

## Stack technique

- **Extension** : React 18, TypeScript, Vite, CRXJS (@crxjs/vite-plugin)
- **Backend** : Python 3.11+, FastAPI, uvicorn, anthropic SDK
- **Modèles** :
  - Orchestrateur : claude-opus-4-6-20251101
  - job_analyzer : claude-haiku-4-5-20251001 (tâche simple, moins cher)
  - cv_adapter : claude-haiku-4-5-20251001 (prompt bien structuré suffit)
- **CV** : backend/data/cv_base.json

## Agents et responsabilités

### Orchestrateur (`backend/agents/orchestrator.py`)
- Reçoit {job_url, job_text} depuis l'extension
- Lance job_analyzer puis passe son output à cv_adapter
- Retourne {job_data, adapted_cv} à l'extension
- Gère les erreurs et timeouts

### Sous-agent 1 : Job Analyzer (`backend/agents/job_analyzer.py`)
- Input : texte brut de l'offre
- Extrait : titre du poste, entreprise, compétences requises (hard + soft),
  niveau d'expérience, valeurs/culture de l'entreprise, missions principales
- Output : JSON structuré
- Modèle : claude-haiku-4-5-20251001

### Sous-agent 2 : CV Adapter (`backend/agents/cv_adapter.py`)
- Input : job_data (JSON) + cv_base.json
- Adapte : résumé personnalisé, réorganise les compétences par pertinence,
  met en avant les projets les plus alignés avec l'offre
- NE JAMAIS inventer des compétences absentes de cv_base.json
- NE JAMAIS modifier les faits (dates, noms d'entreprises, diplômes)
- Output : cv_adapted (JSON, même structure que cv_base.json + champ match_score)
- Modèle : claude-haiku-4-5-20251001

## API Backend — Endpoints MVP
```
POST /analyze-and-adapt   body: {job_url: str, job_text: str}
                          response: {job_data: {}, adapted_cv: {}, match_score: float}
GET  /cv                  response: contenu de cv_base.json
GET  /health              response: {status: "ok"}
```

## Extension Chrome — Comportement MVP

1. Icône active sur : linkedin.com, hellowork.com, jobteaser.com,
   indeed.fr, welcometothejungle.com
2. Popup React avec 3 états :
   - Idle : bouton "Analyser cette offre"
   - Loading : spinner + message étape en cours
   - Result : affiche le match_score + résumé du CV adapté + bouton "Copier le CV"
3. Content script : extrait le texte visible de l'offre (pas tout le HTML)
4. Communication : extension → fetch → backend localhost:8000

## Règles importantes

- **Ne jamais inventer** d'informations sur le candidat
- **Toujours utiliser** les modèles Haiku sauf pour l'orchestrateur
- **Logger** chaque appel API dans backend/logs/{date}.log
- **Variables d'environnement** : python-dotenv, jamais de clé API dans le code
- **CORS** : FastAPI doit accepter les requêtes de l'extension Chrome
- **Timeout** : 30s max par sous-agent, 60s pour le pipeline complet
- **Tests** : un fichier de test par agent dans tests/

## Fichiers sacrés (ne jamais modifier)

- backend/data/cv_base.json
- .env

## Format des commits

`feat(scope): description`
Ex : `feat(job-analyzer): extract skills as structured JSON`

## Definition of Done

Chaque composant doit avoir :
- [ ] Code fonctionnel et testé manuellement
- [ ] Tests unitaires (pytest pour backend, pas de tests E2E)
- [ ] Docstrings sur les fonctions publiques
- [ ] Logs sur les étapes clés

## En fin de session — générer REPORT.md

Avec : ce qui est implémenté, ce qui reste, décisions d'archi, blocages.
```

---

## requirements.txt (allégé, MVP only)
```
fastapi==0.115.0
uvicorn[standard]==0.32.0
anthropic==0.40.0
python-dotenv==1.0.1
pydantic==2.9.0
pytest==8.3.0
pytest-asyncio==0.24.0
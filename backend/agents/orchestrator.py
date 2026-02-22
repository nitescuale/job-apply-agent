"""Orchestrator — pipeline job_analyzer → cv_adapter avec gestion d'erreurs."""
import asyncio
import json
import logging
from pathlib import Path

from anthropic import Anthropic

from .job_analyzer import analyze_job
from .cv_adapter import adapt_cv

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

# Client réservé pour les appels opus au niveau orchestrateur
client = Anthropic()
ORCHESTRATOR_MODEL = "claude-opus-4-6-20251101"


def load_cv_base() -> dict:
    """Charge cv_base.json depuis le répertoire data."""
    cv_path = DATA_DIR / "cv_base.json"
    with open(cv_path, encoding="utf-8") as f:
        return json.load(f)


def _validate_pipeline_output(job_data: dict, adapted_cv: dict) -> bool:
    """Utilise Claude Opus pour valider que les outputs des sous-agents sont cohérents.

    Args:
        job_data: Résultat de job_analyzer
        adapted_cv: Résultat de cv_adapter

    Returns:
        True si les outputs sont valides, False sinon
    """
    logger.info("orchestrator: validating pipeline output with %s", ORCHESTRATOR_MODEL)

    prompt = f"""Vérifie que ces deux JSON sont cohérents entre eux et bien formés.

job_data: {json.dumps(job_data, ensure_ascii=False)}
adapted_cv keys: {list(adapted_cv.keys())}
match_score: {adapted_cv.get('match_score')}

Réponds uniquement par "ok" si tout est correct, ou "error: <raison>" si tu détectes un problème."""

    response = client.messages.create(
        model=ORCHESTRATOR_MODEL,
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
        timeout=10.0,
    )

    answer = response.content[0].text.strip().lower()
    logger.info("orchestrator: validation result = %s", answer)
    return answer.startswith("ok")


async def run_pipeline(job_url: str, job_text: str) -> dict:
    """Lance le pipeline complet : analyse l'offre puis adapte le CV.

    Args:
        job_url: URL de l'offre d'emploi
        job_text: Texte brut extrait de la page

    Returns:
        dict avec job_data, adapted_cv, match_score

    Raises:
        ValueError: Si job_text est vide
        asyncio.TimeoutError: Si le pipeline dépasse 60s
    """
    if not job_text or not job_text.strip():
        raise ValueError("job_text cannot be empty")

    logger.info("orchestrator: starting pipeline for url=%s", job_url)

    async def _run() -> dict:
        # Étape 1 : Analyser l'offre (sync → run in executor)
        logger.info("orchestrator: step 1 — job_analyzer")
        loop = asyncio.get_running_loop()
        job_data = await loop.run_in_executor(None, analyze_job, job_text)

        # Étape 2 : Charger le CV de base
        cv_base = load_cv_base()

        # Étape 3 : Adapter le CV (sync → run in executor)
        logger.info("orchestrator: step 2 — cv_adapter")
        adapted_cv = await loop.run_in_executor(None, adapt_cv, job_data, cv_base)

        # Étape 4 : Validation par l'orchestrateur (Claude Opus)
        logger.info("orchestrator: step 3 — validation")
        is_valid = await loop.run_in_executor(None, _validate_pipeline_output, job_data, adapted_cv)
        if not is_valid:
            logger.warning("orchestrator: validation flagged potential issue in pipeline output")

        return {
            "job_data": job_data,
            "adapted_cv": adapted_cv,
            "match_score": adapted_cv.get("match_score", 0.0),
        }

    # Pipeline complet : timeout 60s
    result = await asyncio.wait_for(_run(), timeout=60.0)
    logger.info("orchestrator: pipeline complete — match_score=%.2f", result["match_score"])
    return result

"""Orchestrator — pipeline job_analyzer → cv_adapter avec gestion d'erreurs."""
import json
import logging
from pathlib import Path

from anthropic import Anthropic

from .job_analyzer import analyze_job
from .cv_adapter import adapt_cv

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

client = Anthropic()  # Pour l'orchestration (claude-opus-4-6-20251101)


def load_cv_base() -> dict:
    """Charge cv_base.json depuis le répertoire data.

    Returns:
        Contenu de cv_base.json sous forme de dict
    """
    cv_path = DATA_DIR / "cv_base.json"
    with open(cv_path, encoding="utf-8") as f:
        return json.load(f)


async def run_pipeline(job_url: str, job_text: str) -> dict:
    """Lance le pipeline complet : analyse l'offre puis adapte le CV.

    Args:
        job_url: URL de l'offre d'emploi
        job_text: Texte brut extrait de la page

    Returns:
        dict avec job_data, adapted_cv, match_score

    Raises:
        ValueError: Si job_text est vide
        TimeoutError: Si un agent dépasse 60s
    """
    if not job_text or not job_text.strip():
        raise ValueError("job_text cannot be empty")

    logger.info("orchestrator: starting pipeline for url=%s", job_url)

    # Étape 1 : Analyser l'offre
    logger.info("orchestrator: step 1 — job_analyzer")
    job_data = analyze_job(job_text)

    # Étape 2 : Charger le CV de base
    cv_base = load_cv_base()

    # Étape 3 : Adapter le CV
    logger.info("orchestrator: step 2 — cv_adapter")
    adapted_cv = adapt_cv(job_data, cv_base)

    match_score = adapted_cv.get("match_score", 0.0)
    logger.info("orchestrator: pipeline complete — match_score=%.2f", match_score)

    return {
        "job_data": job_data,
        "adapted_cv": adapted_cv,
        "match_score": match_score,
    }

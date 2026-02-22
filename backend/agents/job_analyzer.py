"""Job Analyzer agent — extrait les données structurées d'une offre d'emploi."""
import json
import logging
import re

from anthropic import Anthropic

logger = logging.getLogger(__name__)

client = Anthropic()  # lit ANTHROPIC_API_KEY depuis l'env


def _parse_json_response(raw: str) -> dict:
    """Extrait et parse le JSON d'une réponse modèle (gère les blocs markdown)."""
    # Supprimer les fences markdown ```json ... ``` ou ``` ... ```
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("job_analyzer: JSON parse error: %s | raw=%s", e, raw[:200])
        raise ValueError(f"Model returned invalid JSON: {e}") from e


SYSTEM_PROMPT = """Tu es un expert en analyse d'offres d'emploi.
Tu extrais les informations clés d'une offre et les retournes en JSON strictement valide.
Tu ne retournes QUE du JSON, sans texte autour."""


def analyze_job(job_text: str) -> dict:
    """Analyse une offre d'emploi et retourne les données structurées.

    Args:
        job_text: Texte brut de l'offre d'emploi

    Returns:
        dict avec les champs: title, company, required_skills (hard + soft),
        experience_level, culture_values, main_missions
    """
    logger.info("job_analyzer: analyzing job text (%d chars)", len(job_text))

    user_prompt = f"""Analyse cette offre d'emploi et retourne un JSON avec exactement ces champs:
{{
  "title": "titre du poste",
  "company": "nom de l'entreprise",
  "required_skills": {{
    "hard": ["skill1", "skill2"],
    "soft": ["skill1", "skill2"]
  }},
  "experience_level": "junior|mid|senior",
  "culture_values": ["valeur1", "valeur2"],
  "main_missions": ["mission1", "mission2"]
}}

Offre d'emploi:
{job_text}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        timeout=30.0,
    )

    raw = response.content[0].text.strip()
    logger.info("job_analyzer: response received")

    result = _parse_json_response(raw)
    logger.info(
        "job_analyzer: extracted title=%s company=%s",
        result.get("title"),
        result.get("company"),
    )
    return result

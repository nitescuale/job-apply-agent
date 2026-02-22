"""Job Analyzer agent — extrait les données structurées d'une offre d'emploi."""
import json
import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)

client = Anthropic()  # lit ANTHROPIC_API_KEY depuis l'env

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

    # Nettoyer le JSON si entouré de backticks
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw)
    logger.info(
        "job_analyzer: extracted title=%s company=%s",
        result.get("title"),
        result.get("company"),
    )
    return result

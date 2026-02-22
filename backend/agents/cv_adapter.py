"""CV Adapter agent — adapte cv_base.json à une offre d'emploi analysée."""
import json
import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)

client = Anthropic()

SYSTEM_PROMPT = """Tu es un expert en rédaction de CV. Tu adaptes un CV existant à une offre d'emploi.

RÈGLES STRICTES:
- Ne jamais inventer de compétences absentes du CV original
- Ne jamais modifier les faits (dates, noms d'entreprises, diplômes, durées)
- Conserver EXACTEMENT la même structure JSON que le CV original
- Ajouter uniquement un champ "match_score" (float entre 0 et 1)
- Tu ne retournes QUE du JSON valide, sans texte autour"""


def adapt_cv(job_data: dict, cv_base: dict) -> dict:
    """Adapte le CV de base à l'offre d'emploi analysée.

    Args:
        job_data: Données structurées de l'offre (output de job_analyzer)
        cv_base: CV de base (contenu de cv_base.json)

    Returns:
        CV adapté avec même structure que cv_base + champ match_score
    """
    logger.info("cv_adapter: adapting CV for job title=%s", job_data.get("title"))

    user_prompt = f"""Adapte ce CV à l'offre d'emploi suivante.

OFFRE D'EMPLOI ANALYSÉE:
{json.dumps(job_data, ensure_ascii=False, indent=2)}

CV DE BASE (ne jamais modifier les faits, ne jamais inventer de compétences):
{json.dumps(cv_base, ensure_ascii=False, indent=2)}

Retourne le CV adapté en JSON avec:
1. Même structure exacte que le CV de base
2. summary: réécrit pour cibler ce poste spécifique
3. skills: réorganisées par ordre de pertinence pour ce poste
4. projects: réordonnés par pertinence pour ce poste
5. match_score: float entre 0 et 1 (alignement CV/offre)

RAPPEL: ne jamais inventer de compétences ou modifier les faits."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        timeout=30.0,
    )

    raw = response.content[0].text.strip()
    logger.info("cv_adapter: response received")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw)
    logger.info("cv_adapter: match_score=%.2f", result.get("match_score", 0))
    return result

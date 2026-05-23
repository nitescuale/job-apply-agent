"""LLM extractor — moulinette Gemini : filtre le scraping brut et extrait l'essentiel.

Le scraping structurel (job_scraper) renvoie souvent du bruit : menus résiduels,
blabla marketing, HTML mal nettoyé. Cette étape passe les données brutes à Gemini
qui retourne uniquement les informations pratiques, structurées et concises.

L'étape est optionnelle : si GEMINI_API_KEY n'est pas configurée, le pipeline
retourne simplement le scraping brut (voir main.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash"
# Limite la taille du payload envoyé au LLM (free tier + coût tokens)
MAX_PAYLOAD_CHARS = 24000

_SYSTEM = """Tu extrais les informations essentielles d'une offre d'emploi.
On te fournit des données brutes scrapées d'une page web : elles contiennent souvent
du bruit (menus, blabla marketing, HTML résiduel, offres connexes). Ignore ce bruit.

Retourne UNIQUEMENT un objet JSON avec exactement ces clés :
- title: intitulé du poste (string)
- company: nom de l'entreprise (string)
- location: ville ou lieu de travail (string)
- contract_type: type de contrat — CDI, CDD, Alternance, Stage, Freelance, Intérim... (string|null)
- salary: rémunération si mentionnée explicitement, sinon null (string|null)
- remote: politique de télétravail si mentionnée, sinon null (string|null)
- experience_level: niveau ou années d'expérience attendus, sinon null (string|null)
- skills: compétences et technologies requises (array de strings, max 15)
- missions: missions principales du poste, reformulées de façon concise (array de strings, max 8)
- summary: résumé neutre de l'offre en 2-3 phrases, sans blabla marketing (string)

Règles : n'invente jamais une information absente — mets null ou []. Reste factuel.
Réponds avec le JSON seul, sans texte ni balises autour."""


def is_available() -> bool:
    """Indique si la clé API Gemini est configurée dans l'environnement."""
    return bool(os.getenv("GEMINI_API_KEY"))


def _parse_json(text: str) -> dict[str, Any]:
    """Parse la réponse du LLM en tolérant d'éventuelles fences markdown."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


def _call_gemini(prompt: str) -> str:
    """Appelle l'API Gemini et retourne le texte brut de la réponse.

    Isolé dans sa propre fonction pour être facilement mockable en test.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    logger.info("llm_extractor: appel Gemini (%s)", model)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    return response.text or ""


def extract_essentials(scraped: dict[str, Any]) -> dict[str, Any]:
    """Filtre les données scrapées brutes via Gemini et retourne l'essentiel structuré.

    Args:
        scraped: dict renvoyé par scrape_job (potentiellement bruité)

    Returns:
        dict avec les champs essentiels nettoyés : title, company, location,
        contract_type, salary, remote, experience_level, skills, missions, summary.

    Raises:
        RuntimeError: si la clé API n'est pas configurée ou si la réponse est invalide.
    """
    if not is_available():
        raise RuntimeError("GEMINI_API_KEY non configurée")

    payload = json.dumps(scraped, ensure_ascii=False)[:MAX_PAYLOAD_CHARS]
    prompt = f"{_SYSTEM}\n\nDonnées brutes scrapées :\n{payload}"

    raw = _call_gemini(prompt)
    try:
        data = _parse_json(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("llm_extractor: réponse non-JSON — %r", raw[:200])
        raise RuntimeError(f"Gemini a renvoyé du JSON invalide: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Gemini a renvoyé un JSON qui n'est pas un objet")

    logger.info(
        "llm_extractor: extrait — %d skills, %d missions",
        len(data.get("skills") or []),
        len(data.get("missions") or []),
    )
    return data

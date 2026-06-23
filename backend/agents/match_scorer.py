"""match_scorer — score de pertinence (0-100) offre / profil utilisateur.

Cascade :
  1. GEMINI_API_KEY présente → Gemini renvoie un JSON strict
     {score, matched_skills, missing_skills, rationale}.
  2. Pas de clé ou Gemini en erreur → fallback déterministe par overlap des
     skills (matched / offer_skills × 100), llm_used=False.

`score_match` n'élève jamais d'exception : toute erreur est loggée puis
entraîne un repli sur le calcul hors-ligne. Garantit que la jauge
extension est toujours rendue, quel que soit l'état du LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_PAYLOAD_CHARS = 18000

_SYSTEM = """Tu évalues l'adéquation entre une offre d'emploi et un profil candidat.

Tu reçois :
- L'offre (title, company, missions, skills requis, summary, ...).
- Le profil (current_title, years_experience, education, skills, summary).

Tu renvoies UNIQUEMENT un objet JSON avec exactement ces clés :
- score: entier entre 0 et 100 (100 = adéquation parfaite, 0 = aucun recouvrement).
- matched_skills: array de strings — skills demandés par l'offre que le candidat possède explicitement.
- missing_skills: array de strings — skills demandés mais absents du profil.
- rationale: phrase courte (≤ 220 caractères) qui justifie le score.

Règles strictes :
- Jamais d'invention : matched_skills ne contient QUE des skills présents dans le profil.
- Reformule les skills tels qu'écrits dans l'offre (ex : "PyTorch" si l'offre dit PyTorch).
- Si l'offre n'a pas de skills explicites, infère raisonnablement depuis missions/title.
- Le score pondère skills + expérience + éducation. Ne descends pas sous 30 sans raison forte.
- Réponds avec le JSON seul, sans texte ni balises autour."""


# ──────────────────────────────────────────────────────────────────────────
# Helpers — partagés Gemini + fallback
# ──────────────────────────────────────────────────────────────────────────


def is_available() -> bool:
    """Indique si la clé API Gemini est configurée."""
    return bool(os.getenv("GEMINI_API_KEY"))


def _parse_json(text: str) -> dict[str, Any]:
    """Parse la réponse LLM en tolérant les fences markdown ```json ... ```."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


def _call_gemini(prompt: str) -> str:
    """Appelle Gemini et retourne le texte. Isolé pour mockabilité."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    logger.info("match_scorer: appel Gemini (%s)", model)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    return response.text or ""


def _normalize(s: Any) -> str:
    """NFKD + ASCII + lowercase + collapse whitespace — pour comparer skills."""
    s = unicodedata.normalize("NFKD", str(s))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _clamp_score(v: Any) -> int:
    """Force le score dans [0, 100]. Cast tolérant (None/str/float → int)."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _extract_profile_skills(profile: dict[str, Any]) -> list[str]:
    """Aplatit profile.skills (peut être une liste ou un dict par catégorie)."""
    raw = profile.get("skills") or []
    out: list[str] = []
    if isinstance(raw, dict):
        for v in raw.values():
            if isinstance(v, list):
                out.extend(str(x) for x in v if x)
    elif isinstance(raw, list):
        out.extend(str(x) for x in raw if x)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Fallback déterministe (sans LLM)
# ──────────────────────────────────────────────────────────────────────────


def _fallback_score(offer: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Coverage des skills de l'offre par le profil — overlap simple.

    On préfère overlap à Jaccard pour ce cas d'usage : un candidat avec
    BEAUCOUP de skills extras ne doit pas être pénalisé. Le score
    représente "% des skills requis que je possède".
    """
    offer_skills_raw = [str(s) for s in (offer.get("skills") or []) if s]
    profile_skills_raw = _extract_profile_skills(profile)

    profile_norm = {_normalize(s) for s in profile_skills_raw if _normalize(s)}
    matched: list[str] = []
    missing: list[str] = []
    seen_norm: set[str] = set()
    for s in offer_skills_raw:
        n = _normalize(s)
        if not n or n in seen_norm:
            continue
        seen_norm.add(n)
        if n in profile_norm:
            matched.append(s)
        else:
            missing.append(s)

    total = len(matched) + len(missing)
    if total == 0:
        score = 0
        rationale = "Aucune compétence détectée dans l'offre — score hors-ligne à 0."
    else:
        score = round(len(matched) / total * 100)
        rationale = (
            f"Score hors-ligne : {len(matched)}/{total} compétences "
            f"de l'offre couvertes par le profil."
        )
    return {
        "score": _clamp_score(score),
        "matched_skills": matched,
        "missing_skills": missing,
        "rationale": rationale,
        "llm_used": False,
    }


# ──────────────────────────────────────────────────────────────────────────
# LLM path
# ──────────────────────────────────────────────────────────────────────────


def _profile_for_llm(profile: dict[str, Any]) -> dict[str, Any]:
    """Sous-ensemble du profil utile au scoring — réduit la consommation de tokens.

    On ne passe pas email, téléphone, cv_path, etc. Inutile et c'est du PII.
    """
    return {
        "current_title": profile.get("current_title"),
        "years_experience": profile.get("years_experience"),
        "education_level": profile.get("education_level"),
        "education_field": profile.get("education_field"),
        "school": profile.get("school"),
        "skills": profile.get("skills"),
        "languages": profile.get("languages"),
        "summary": profile.get("summary"),
    }


def _llm_score(offer: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Appel Gemini, parsing strict, clamp. Raise sur réponse invalide."""
    payload = {"offer": offer, "profile": _profile_for_llm(profile)}
    payload_json = json.dumps(payload, ensure_ascii=False)[:MAX_PAYLOAD_CHARS]
    prompt = f"{_SYSTEM}\n\nDonnées :\n{payload_json}"

    raw = _call_gemini(prompt)
    try:
        data = _parse_json(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("match_scorer: réponse non-JSON — %r", raw[:200])
        raise RuntimeError(f"Gemini a renvoyé du JSON invalide: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Gemini a renvoyé un JSON qui n'est pas un objet")

    matched = list(data.get("matched_skills") or [])
    missing = list(data.get("missing_skills") or [])
    rationale = str(data.get("rationale") or "").strip()
    if len(rationale) > 280:
        rationale = rationale[:277] + "..."

    return {
        "score": _clamp_score(data.get("score")),
        "matched_skills": [str(s) for s in matched if s],
        "missing_skills": [str(s) for s in missing if s],
        "rationale": rationale,
        "llm_used": True,
    }


# ──────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────


def score_match(offer: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Renvoie {score, matched_skills, missing_skills, rationale, llm_used}.

    Ne lève jamais — toute erreur LLM est loggée puis on retombe sur le
    calcul hors-ligne. Garantit une jauge toujours rendue côté extension.
    """
    if is_available():
        try:
            return _llm_score(offer, profile)
        except (json.JSONDecodeError, RuntimeError, KeyError) as exc:
            logger.error("match_scorer: LLM en erreur, fallback overlap — %s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("match_scorer: erreur LLM inattendue, fallback overlap")
    else:
        logger.info("match_scorer: GEMINI_API_KEY absente, fallback overlap")
    return _fallback_score(offer, profile)

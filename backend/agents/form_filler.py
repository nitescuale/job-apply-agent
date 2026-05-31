"""Form filler — mappe un schéma de formulaire à un profil utilisateur via Gemini.

Le content script de l'extension détecte les champs d'un <form> et envoie leur
schéma au backend. Gemini reçoit le schéma + le profil et renvoie un mapping
`{field_id: value}` strict (n'invente rien, laisse les champs incertains vides).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PROFILE_PATH = DATA_DIR / "user_profile.json"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_PAYLOAD_CHARS = 16000

_SYSTEM = """Tu remplis un formulaire de candidature d'emploi à partir d'un profil utilisateur.
On te fournit deux objets JSON :
- form_schema : la liste des champs du formulaire (id, label, type, required, options, placeholder)
- profile : les infos de l'utilisateur (nom, email, expérience, lettre type, etc.)
- context (optionnel) : infos sur l'offre courante (title, company) pour personnaliser la lettre

Pour chaque champ, décide la valeur la plus adaptée en croisant le label et le profil.

Règles strictes :
- N'invente jamais une information absente du profil. Si tu n'es pas confiant, omets le champ.
- Pour les select/radio, choisis EXACTEMENT une option de la liste fournie, sinon omets.
- Pour les checkbox, renvoie true/false uniquement si le label le justifie clairement.
- Pour les textarea (motivation, présentation), adapte le `cover_letter_template` au job courant
  en injectant `{title}` et `{company}` depuis context si présent. Reste sobre, 4-6 lignes max.
- Pour les fichiers (type=file), renvoie "__CV__" si le profil a cv_path, sinon omets.
- Ignore les champs purement décoratifs (h1, hidden, captcha).

Réponds avec UNIQUEMENT un objet JSON plat de la forme {field_id: value}.
Pas de texte autour, pas de fences markdown."""


def is_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY")) and PROFILE_PATH.exists()


def load_profile() -> dict[str, Any]:
    """Charge user_profile.json depuis backend/data/.

    Raises:
        FileNotFoundError: si user_profile.json n'existe pas (l'utilisateur doit
            copier user_profile.example.json et l'adapter).
    """
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"user_profile.json absent. Copie user_profile.example.json vers "
            f"{PROFILE_PATH} et adapte-le."
        )
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_cv_base64() -> str | None:
    """Lit le CV référencé par profile.cv_path et le renvoie en base64.

    Retourne None si pas de cv_path ou fichier introuvable (l'extension
    skippera l'upload programmatique).
    """
    try:
        profile = load_profile()
    except FileNotFoundError:
        return None
    cv_path = profile.get("cv_path")
    if not cv_path:
        return None
    path = Path(cv_path).expanduser()
    if not path.is_file():
        logger.warning("form_filler: cv_path défini mais fichier introuvable: %s", path)
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


def _call_gemini(prompt: str) -> str:
    """Appelle Gemini avec response_mime_type JSON. Isolé pour faciliter le mock."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    logger.info("form_filler: appel Gemini (%s)", model)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return response.text or ""


def fill_form(form_schema: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mappe les champs du formulaire à des valeurs depuis le profil utilisateur.

    Args:
        form_schema: {"fields": [{"id", "label", "type", "required", "options"?, ...}, ...]}
        context: infos sur l'offre courante (title, company) pour personnaliser la lettre

    Returns:
        dict {"values": {field_id: value}, "cv_base64": str|None}
        Les valeurs textuelles sont des strings, booléennes pour checkbox,
        et "__CV__" comme sentinelle pour les inputs type=file.

    Raises:
        FileNotFoundError: si user_profile.json absent
        RuntimeError: si la clé API absente ou Gemini renvoie du JSON invalide
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY non configurée")

    profile = load_profile()

    payload = {
        "form_schema": form_schema,
        "profile": profile,
        "context": context or {},
    }
    serialized = json.dumps(payload, ensure_ascii=False)[:MAX_PAYLOAD_CHARS]
    prompt = f"{_SYSTEM}\n\n{serialized}"

    raw = _call_gemini(prompt)
    try:
        values = _parse_json(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("form_filler: JSON invalide — %r", raw[:200])
        raise RuntimeError(f"Gemini a renvoyé du JSON invalide: {exc}") from exc

    if not isinstance(values, dict):
        raise RuntimeError("Gemini n'a pas renvoyé un objet JSON")

    cv_b64 = get_cv_base64()
    logger.info("form_filler: %d champs remplis, CV=%s", len(values), bool(cv_b64))

    return {"values": values, "cv_base64": cv_b64}

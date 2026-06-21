"""CV tailor — adapte le CV DOCX de base à une offre EN PRÉSERVANT sa forme.

Différence majeure avec une approche Markdown -> PDF : on n'invente PAS de
mise en page. Le DOCX de l'utilisateur est ouvert, on identifie les
paragraphes "éditables" (descriptions longues, bullets de réalisations,
summary), on demande à Gemini une version tailorée de leur texte, et on
réécrit le contenu textuel des runs en gardant le formatting (font, gras,
italique, alignement, espacement) tel qu'il est dans le DOCX. Le résultat
est un nouveau DOCX visuellement identique à l'original, sauf que les
bullets sont reformulés pour mirrorer l'offre. On convertit ensuite ce
DOCX en PDF via docx2pdf (Microsoft Word COM) ou LibreOffice headless en
fallback.

Pipeline :
    1. Charge profile.base_cv_path avec python-docx.
    2. Walk paragraphes top-level + paragraphes dans les cellules de table.
    3. Heuristique _is_editable filtre : assez long, pas de tab,
       pas en MAJUSCULES seules, pas d'info contact (email, téléphone, URL).
    4. Gemini reçoit (indices, texte original, offre, profil) → renvoie
       un JSON {idx: nouveau_texte}. On respecte la taille (±25%) et la
       structure (pas d'ajout/suppression de paragraphes).
    5. Pour chaque édition, on réécrit le run[0] et on vide les autres
       runs du paragraphe → formatting préservé, texte changé.
    6. Sauvegarde DOCX + conversion PDF dans
       {cv_output_dir}/{Company}/0_cv_*.{docx,pdf}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from .form_filler import load_profile

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# Clichés bannis dans le prompt. Conservés comme constante pour que les
# tests puissent vérifier qu'ils sont absents d'une sortie type.
BANNED_CLICHES: tuple[str, ...] = (
    "passionate",
    "driven",
    "hard-working",
    "hard working",
    "team player",
    "fast learner",
    "hit the ground running",
    "always on the lookout",
    "results-oriented",
    "results oriented",
    "self-motivated",
    "self motivated",
)

_SYSTEM = """You tailor a candidate's CV to a specific job offer by REWRITING
SPECIFIC TEXT PARAGRAPHS, preserving the candidate's exact CV layout.

You receive:
  - The job OFFER (title, company, required skills, missions, ...).
  - The candidate's structured PROFILE (contact, languages, etc.).
  - A numbered list of EDITABLE_PARAGRAPHS from the candidate's DOCX. Each
    entry is "{idx}: {original text}". These are the substantive paragraphs
    (summary, bullets, project descriptions). Other paragraphs (name,
    section headers, dates, locations, contact info) have ALREADY been
    excluded — never reference them, never produce keys for them.

For each paragraph, decide whether to rewrite or keep it:
  - Rewrite ONLY when you can make the bullet more relevant to the offer
    while staying strictly factual (use only facts contained in the
    original paragraph, the PROFILE, or the OFFER as keywords).
  - Stay within ±25 % of the original character count.
  - Keep one paragraph per index — DO NOT merge, split, add or remove
    bullets.
  - Use English action verbs. Mirror offer keywords when truthful.
  - NEVER invent metrics, employers, titles, technologies, or dates that
    are not in the original paragraph.

NEVER use these clichés (even once): passionate, driven, hard-working,
team player, fast learner, hit the ground running, always on the
lookout, results-oriented, self-motivated.

Output a STRICT JSON object. Keys are paragraph indices (as strings).
Values are the rewritten text. Omit a key if you choose to keep that
paragraph verbatim. No fences, no preamble — JSON only."""


# ──────────────────────────────────────────────────────────────────────────
# Availability + paths
# ──────────────────────────────────────────────────────────────────────────


def is_available() -> bool:
    """Vrai si la clé Gemini et le profil (avec base_cv_path + cv_output_dir)
    sont configurés."""
    if not os.getenv("GEMINI_API_KEY"):
        return False
    try:
        profile = load_profile()
    except FileNotFoundError:
        return False
    return bool(profile.get("base_cv_path")) and bool(profile.get("cv_output_dir"))


def read_base_cv(path: str | Path) -> str:
    """Extrait le texte brut d'un DOCX (paragraphes + cellules de table).

    Conservé pour diagnostics / tests. Le pipeline de tailoring n'utilise plus
    cette fonction — il manipule directement l'objet Document pour pouvoir
    préserver le formatting des runs lors du remplacement.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"base_cv_path introuvable: {p}")
    if p.suffix.lower() != ".docx":
        raise ValueError(f"base_cv_path doit être un .docx (reçu: {p.suffix})")

    from docx import Document

    doc = Document(str(p))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                t = cell.text.strip()
                if t:
                    parts.append(t)
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────
# Sanitization + filename convention
# ──────────────────────────────────────────────────────────────────────────


def _slug(s: str | None, *, allow_caps: bool = False) -> str:
    """Normalise une chaîne pour un nom de fichier/dossier ASCII."""
    if not s:
        return ""
    text = unicodedata.normalize("NFKD", s)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[\s/\\\-]+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text if allow_caps else text.lower()


def make_filename(profile: dict[str, Any], offer: dict[str, Any]) -> str:
    """Convention : 0_cv_firstname_lastname_jobtitle_company.pdf"""
    parts = [
        "0",
        "cv",
        _slug(profile.get("first_name")) or "x",
        _slug(profile.get("last_name")) or "x",
        _slug(offer.get("title")) or "role",
        _slug(offer.get("company")) or "company",
    ]
    base = "_".join(p for p in parts if p)
    return f"{base}.pdf"


def resolve_output_path(profile: dict[str, Any], offer: dict[str, Any]) -> Path:
    """{cv_output_dir}/{Company_Sanitized}/{filename}.pdf"""
    root = profile.get("cv_output_dir", "").strip()
    if not root:
        raise ValueError("profile.cv_output_dir n'est pas configuré")
    company_dir = _slug(offer.get("company"), allow_caps=True) or "Unknown_Company"
    return Path(root).expanduser() / company_dir / make_filename(profile, offer)


# ──────────────────────────────────────────────────────────────────────────
# DOCX walking + editable heuristic
# ──────────────────────────────────────────────────────────────────────────

_CONTACT_RE = re.compile(
    r"(\+\d{2,3}\s*\d|https?://|linkedin\.|github\.|\.com\b|\.fr\b|\bphone\b|\btel\b)",
    re.IGNORECASE,
)


def _is_editable(text: str) -> bool:
    """Heuristique : un paragraphe est éditable s'il contient du contenu
    substantiel (un bullet de réalisation, le summary, une description de
    projet) plutôt que de la mise en page (entête de section, ligne de
    contact, ligne tab-séparée company/location).

    Règles cumulatives — toutes doivent être vraies :
      - longueur ≥ 25 caractères (filtre les section headers, noms, dates)
      - pas de tabulation (filtre les lignes alignées droite tab-séparées)
      - pas en MAJUSCULES seules (filtre EXPERIENCE, EDUCATION, ...)
      - pas d'@ (filtre les emails)
      - pas de pattern contact (téléphone, URL, github/linkedin)
    """
    s = text.strip()
    if len(s) < 25:
        return False
    if "\t" in s:
        return False
    # Tous-caps seuls (ignorant ponctuation) → en-tête de section
    letters = re.sub(r"[^A-Za-z]", "", s)
    if letters and letters == letters.upper() and len(s) < 80:
        return False
    if "@" in s:
        return False
    if _CONTACT_RE.search(s):
        return False
    return True


def _collect_paragraphs(doc: Any) -> list[Any]:
    """Walk top-level paragraphs + table cell paragraphs, dans l'ordre de
    lecture. Retourne la liste des objets Paragraph (python-docx).
    L'index dans cette liste sert de clé pour les éditions Gemini.
    """
    paras: list[Any] = []
    for p in doc.paragraphs:
        paras.append(p)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    paras.append(p)
    return paras


def _set_paragraph_text(para: Any, new_text: str) -> None:
    """Remplace le texte d'un paragraphe en conservant le formatting de son
    premier run (font, gras, italique, couleur, taille). Les runs suivants
    sont vidés.

    Compromis assumé : un paragraphe avec runs de styles mixtes (e.g.
    moitié bold / moitié plain) perd cette mixité — l'ensemble prend le
    style du premier run. Sur les bullets d'un CV typique c'est invisible
    parce que le formatting est uniforme par paragraphe.
    """
    runs = para.runs
    if not runs:
        para.add_run(new_text)
        return
    runs[0].text = new_text
    for r in runs[1:]:
        r.text = ""


# ──────────────────────────────────────────────────────────────────────────
# Gemini call
# ──────────────────────────────────────────────────────────────────────────


def _call_gemini(prompt: str) -> str:
    """Appelle Gemini en mode JSON. Isolé pour faciliter le mock."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    logger.info("cv_tailor: appel Gemini (%s)", model)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


def _build_prompt(
    editables: list[tuple[int, str]],
    offer: dict[str, Any],
    profile: dict[str, Any],
) -> str:
    """Construit le prompt avec les paragraphes numérotés à tailorer."""
    offer_compact = {
        k: v
        for k, v in offer.items()
        if v and k not in ("description", "url")
    }
    paragraphs_block = "\n".join(f"{idx}: {text}" for idx, text in editables)
    return (
        f"{_SYSTEM}\n\n"
        f"--- OFFER ---\n{json.dumps(offer_compact, ensure_ascii=False, indent=2)}\n\n"
        f"--- PROFILE ---\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"--- EDITABLE_PARAGRAPHS ---\n{paragraphs_block}\n"
    )


def _parse_edits(raw: str) -> dict[int, str]:
    """Parse la réponse JSON de Gemini en {idx: nouveau_texte}.

    Tolère un wrapper code-fence si Gemini en remet malgré le mime-type.
    """
    cleaned = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned:
        return {}
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini JSON invalide: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Gemini n'a pas renvoyé un objet JSON")
    edits: dict[int, str] = {}
    for k, v in payload.items():
        try:
            edits[int(k)] = str(v).strip()
        except (TypeError, ValueError):
            logger.warning("cv_tailor: clé non-entière ignorée — %r", k)
    return edits


# ──────────────────────────────────────────────────────────────────────────
# DOCX → PDF conversion
# ──────────────────────────────────────────────────────────────────────────


def _convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    """Convertit un DOCX en PDF. Essaie docx2pdf (Word COM) puis LibreOffice.

    docx2pdf utilise Microsoft Word via pywin32/COM sur Windows. Si Word
    n'est pas installé, l'appel lève une exception et on bascule sur
    `soffice --headless`. Si ni l'un ni l'autre n'est disponible, on lève
    une RuntimeError avec instructions explicites pour l'utilisateur.
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # Path 1 : Microsoft Word via docx2pdf (qualité PDF maximale,
    # rendu 1:1 fidèle au DOCX)
    try:
        from docx2pdf import convert as _w_convert

        _w_convert(str(docx_path), str(pdf_path))
        if pdf_path.is_file():
            logger.info("cv_tailor: PDF via docx2pdf -> %s", pdf_path)
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cv_tailor: docx2pdf indisponible (%s), fallback LibreOffice", exc
        )

    # Path 2 : LibreOffice headless
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(pdf_path.parent),
                    str(docx_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            produced = pdf_path.parent / f"{docx_path.stem}.pdf"
            if produced != pdf_path and produced.is_file():
                produced.replace(pdf_path)
            logger.info("cv_tailor: PDF via LibreOffice -> %s", pdf_path)
            return
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            logger.error("LibreOffice a échoué: %s", stderr)

    raise RuntimeError(
        "Conversion DOCX -> PDF impossible : ni Microsoft Word (via docx2pdf) "
        "ni LibreOffice (soffice) ne sont disponibles. Installe l'un des deux."
    )


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────


def tailor_cv(offer: dict[str, Any]) -> dict[str, Any]:
    """Édite en place les paragraphes éditables du DOCX base et exporte en PDF.

    Args:
        offer: champs structurés de l'offre (title, company, skills, ...).

    Returns:
        {
            "saved_path": str,           # chemin absolu du PDF final
            "saved_docx_path": str,      # chemin absolu du DOCX tailoré
            "filename": str,             # nom du PDF seul
            "folder": str,               # dossier entreprise
            "edited_count": int,         # nb de paragraphes effectivement modifiés
            "editable_count": int,       # nb de paragraphes envoyés au LLM
        }

    Raises:
        FileNotFoundError: profil ou base_cv_path absent
        ValueError: cv_output_dir manquant ou DOCX invalide
        RuntimeError: clé API absente, Gemini KO, ou conversion PDF impossible
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY non configurée")

    profile = load_profile()
    base_path = profile.get("base_cv_path", "").strip()
    if not base_path:
        raise ValueError("profile.base_cv_path n'est pas configuré (DOCX source manquant)")

    base = Path(base_path).expanduser()
    if not base.is_file():
        raise FileNotFoundError(f"base_cv_path introuvable: {base}")
    if base.suffix.lower() != ".docx":
        raise ValueError(f"base_cv_path doit être un .docx (reçu: {base.suffix})")

    output_pdf = resolve_output_path(profile, offer)
    output_docx = output_pdf.with_suffix(".docx")

    # 1. Charger le DOCX
    from docx import Document

    doc = Document(str(base))
    paragraphs = _collect_paragraphs(doc)

    # 2. Identifier les paragraphes éditables
    editables: list[tuple[int, str]] = [
        (i, p.text) for i, p in enumerate(paragraphs) if _is_editable(p.text)
    ]
    if not editables:
        raise ValueError(
            "Aucun paragraphe éditable détecté dans le DOCX (CV vide ou structure inattendue)"
        )
    logger.info("cv_tailor: %d paragraphes éditables détectés", len(editables))

    # 3. Demander à Gemini les versions tailorées
    prompt = _build_prompt(editables, offer, profile)
    raw = _call_gemini(prompt)
    edits = _parse_edits(raw)
    logger.info("cv_tailor: %d éditions reçues sur %d demandées", len(edits), len(editables))

    # 4. Appliquer les éditions, en gardant l'index original
    editable_idxs = {idx for idx, _ in editables}
    edited_count = 0
    for idx, new_text in edits.items():
        if idx not in editable_idxs:
            logger.warning("cv_tailor: idx %d hors liste editable, ignoré", idx)
            continue
        if not new_text:
            continue
        _set_paragraph_text(paragraphs[idx], new_text)
        edited_count += 1

    # 5. Sauvegarder DOCX puis convertir en PDF
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx))
    _convert_docx_to_pdf(output_docx, output_pdf)

    logger.info(
        "cv_tailor: PDF généré -> %s (%d/%d éditions appliquées)",
        output_pdf,
        edited_count,
        len(editables),
    )
    return {
        "saved_path": str(output_pdf),
        "saved_docx_path": str(output_docx),
        "filename": output_pdf.name,
        "folder": str(output_pdf.parent),
        "edited_count": edited_count,
        "editable_count": len(editables),
    }

"""CV tailor — adapte un CV de base (DOCX) à une offre via Gemini, rend en PDF.

Pipeline :
    1. Lit le DOCX référencé par profile.base_cv_path → texte brut.
    2. Demande à Gemini de réécrire le CV en Markdown anglais, orienté ATS,
       en miroitant les mots-clés de l'offre sans inventer de faits.
    3. Convertit le Markdown → HTML → PDF (xhtml2pdf, pure Python — pas de
       dépendance GTK/Pango sous Windows comme avec WeasyPrint).
    4. Sauvegarde dans {cv_output_dir}/{Company_Sanitized}/0_cv_*.pdf
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from .form_filler import load_profile

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_BASE_CV_CHARS = 18000
SUMMARY_MIN_LENGTH = 30

# Banned cliché phrases — kept as a module constant so tests can import and
# enforce. Case-insensitive substring match.
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

_SYSTEM = """You are a senior CV tailoring expert. You receive:
1. A base CV (free-form text extracted from a DOCX) — the candidate's full history.
2. A job offer with extracted essentials (title, company, required skills, missions, etc.).
3. A structured profile with contact details and facts.

Generate a tailored CV in **English Markdown** with a STRICT structure designed
for a clean two-column layout (left = content, right = date/location). Use the
EXACT '||' separator (two pipes) to mark left/right splits.

# {Full Name}
{Tagline — one short line, e.g. "2026 Data Scientist / AI&ML Engineer Graduate"}
{Contact line, ONE line: City, Country — Phone — Email — github.com/handle — linkedin.com/in/handle}

## EXPERIENCE

### {Company} || {Location}
{Optional one-line company description, plain prose}
**{Job Title}** || *{Date range}*
- {Action-verb achievement, mirrors offer keywords, never invents}
- {Another bullet}

(repeat the block above per job)

## PROJECTS

### {Project name}
{One sentence describing what it is and the stack}
- {Bullet}
- {Bullet}

## EDUCATION

### {School} || {Location}
**{Program / Degree}** || *{Year range}*
{Optional one-line list of relevant coursework}

## SKILLS
{Comma-separated, offer-relevant skills first, grouped if natural}

## LANGUAGES
- {Language}: {Level}

Hard rules:
- Output English Markdown ONLY. No fences, no preamble.
- The '||' MUST appear exactly once per header line (no spaces around them
  matter, the parser trims). If a line has no right-hand value, OMIT the line.
- Do NOT invent dates, titles, employers, or achievements absent from the
  base CV / profile.
- Do NOT add a "Summary", "Profile" or "Objective" section — a separate pass
  prepends the SUMMARY.
- Target one page (~600 words). Action verbs, no clichés."""


_SUMMARY_SYSTEM = """You write the top-of-CV professional summary that appears
above Experience on a one-page tailored CV.

Output ONLY the summary text. 2 to 3 sentences. Plain prose, no preamble,
no markdown, no quotes, no bullets, no header.

HARD RULES:
- Every sentence must reference a concrete, verifiable fact taken from PROFILE
  or BASE_CV (a technology, a metric, a project, an experience, a year, a
  school). Never invent metrics, employers, titles, technologies or dates.
- Mirror 2-4 keywords from the OFFER (job title and required skills) — only
  those genuinely supported by PROFILE/BASE_CV. Never claim a skill the
  candidate does not have.
- Open with positioning aligned to the offer's job title.
- Language: English only.

BANNED PHRASES (never use, even once): "passionate", "driven", "hard-working",
"team player", "fast learner", "hit the ground running", "always on the
lookout", "results-oriented", "self-motivated".

NEVER state ambitions that could disqualify the candidate for THIS offer:
- No "looking to move into management" on an IC role.
- No "transitioning into research" on an applied role.
- If you mention a goal, derive it from the offer itself.

Output: 2 to 3 sentences of prose. Nothing else."""


# ──────────────────────────────────────────────────────────────────────────
# Availability + DOCX reading
# ──────────────────────────────────────────────────────────────────────────


def is_available() -> bool:
    """Vrai si la clé Gemini et le profil sont configurés."""
    if not os.getenv("GEMINI_API_KEY"):
        return False
    try:
        profile = load_profile()
    except FileNotFoundError:
        return False
    return bool(profile.get("base_cv_path")) and bool(profile.get("cv_output_dir"))


def read_base_cv(path: str | Path) -> str:
    """Extrait le texte d'un DOCX. Tolère .docx uniquement (pas .doc legacy)."""
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
    # Tableaux (parfois utilisés pour les sections du CV)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                t = cell.text.strip()
                if t:
                    parts.append(t)
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────
# Sanitization + paths
# ──────────────────────────────────────────────────────────────────────────


def _slug(s: str | None, *, allow_caps: bool = False) -> str:
    """Normalise une chaîne pour un nom de fichier/dossier ASCII.

    - décompose les accents (é -> e)
    - remplace espaces et séparateurs par _
    - retire le reste hors [a-zA-Z0-9_]
    """
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
# LLM call
# ──────────────────────────────────────────────────────────────────────────


def _strip_md_fences(text: str) -> str:
    cleaned = text.strip()
    fence = re.match(r"^```(?:markdown|md)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return cleaned


def _call_gemini(prompt: str) -> str:
    """Appelle Gemini en mode texte (pas JSON). Isolé pour faciliter le mock."""
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
            response_mime_type="text/plain",
        ),
    )
    return response.text or ""


def _build_prompt(base_cv: str, offer: dict[str, Any], profile: dict[str, Any]) -> str:
    """Construit le prompt avec sections délimitées pour Gemini."""
    import json

    offer_compact = {k: v for k, v in offer.items() if v and k not in ("description", "url")}
    return (
        f"{_SYSTEM}\n\n"
        f"--- BASE CV (DOCX text) ---\n{base_cv[:MAX_BASE_CV_CHARS]}\n\n"
        f"--- OFFER ESSENTIALS ---\n{json.dumps(offer_compact, ensure_ascii=False, indent=2)}\n\n"
        f"--- PROFILE FACTS ---\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n"
    )


# ──────────────────────────────────────────────────────────────────────────
# Summary generation (separate Gemini pass, optional)
# ──────────────────────────────────────────────────────────────────────────


def _clean_summary(raw: str) -> str:
    """Trim quotes, fences and stray markdown headings from the LLM output."""
    text = raw.strip()
    # Strip surrounding quotes (single, double, smart)
    for q in ('"', "'", "“", "”", "‘", "’"):
        if text.startswith(q) and text.endswith(q) and len(text) > 1:
            text = text[1:-1].strip()
    # Strip accidental code fence
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Strip accidental leading heading marker
    text = re.sub(r"^#+\s*", "", text).strip()
    # Drop a leading "Summary" line — handles both "Summary: ..." (inline)
    # and "Summary\n..." (heading-style)
    text = re.sub(
        r"^(summary|profile|objective)\s*[:\-—]\s*", "", text, flags=re.IGNORECASE
    ).strip()
    first_line, *rest = text.split("\n", 1)
    if first_line.strip().lower() in {"summary", "profile", "objective"} and rest:
        text = rest[0].strip()
    return text


def generate_summary(
    offer: dict[str, Any],
    profile: dict[str, Any],
    base_cv_text: str = "",
) -> str | None:
    """Generate a 2-3 sentence ATS-friendly summary tailored to the offer.

    Args:
        offer: structured offer essentials (title, company, skills, missions, ...).
        profile: user_profile.json content (facts only, not the cover letter).
        base_cv_text: raw text extracted from the candidate's base DOCX. Provides
            additional concrete facts the structured profile may not expose.

    Returns:
        The cleaned summary string, or None if the step is disabled, the LLM
        is unavailable, returns empty/short/invalid output, or any unexpected
        error occurs. Never raises — the caller falls back to no summary.
    """
    if not os.getenv("GEMINI_API_KEY"):
        logger.info("cv_tailor: summary skipped (no GEMINI_API_KEY)")
        return None

    import json

    offer_compact = {
        k: v
        for k, v in offer.items()
        if v and k in {"title", "company", "skills", "missions", "experience_level"}
    }
    prompt = (
        f"{_SUMMARY_SYSTEM}\n\n"
        f"--- OFFER ---\n{json.dumps(offer_compact, ensure_ascii=False, indent=2)}\n\n"
        f"--- PROFILE ---\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"--- BASE_CV ---\n{base_cv_text[:8000]}"
    )

    try:
        raw = _call_gemini(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cv_tailor: summary generation failed — %s", exc)
        return None

    cleaned = _clean_summary(raw or "")
    if not cleaned or len(cleaned) < SUMMARY_MIN_LENGTH:
        logger.info("cv_tailor: summary empty or too short, skipping")
        return None

    return cleaned


def _inject_summary(md: str, summary: str) -> str:
    """Insert a '## SUMMARY' section before the first existing '## ' heading.

    If the Markdown has no other '## ' section, the SUMMARY block is appended
    at the end. Idempotent on a SUMMARY-free input.
    """
    if not summary:
        return md
    block = f"## SUMMARY\n{summary}\n\n"
    lines = md.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[:i]) + ("\n" if i else "") + block + "\n".join(lines[i:])
    # No H2 section — append at the end
    return md.rstrip() + "\n\n" + block.rstrip() + "\n"


# ──────────────────────────────────────────────────────────────────────────
# Markdown → PDF
# ──────────────────────────────────────────────────────────────────────────

_PDF_CSS = """
@page { size: A4; margin: 1.4cm 1.6cm; }
body {
  font-family: 'Times', 'Times New Roman', serif;
  font-size: 10.5pt;
  color: #1a1a1a;
  line-height: 1.38;
}

/* Header — centered, à la traditional CV */
h1 {
  font-size: 19pt;
  font-weight: 700;
  text-align: center;
  margin: 0 0 2pt;
}
.tagline {
  text-align: center;
  font-size: 12pt;
  font-weight: 700;
  margin: 0 0 2pt;
}
.contact {
  text-align: center;
  font-size: 10pt;
  color: #333;
  margin: 0 0 12pt;
}

/* Section headers — uppercase + souligné */
h2 {
  font-size: 11pt;
  font-weight: 700;
  text-transform: uppercase;
  margin: 11pt 0 4pt;
  border-bottom: 0.6pt solid #000;
  padding-bottom: 1pt;
}

/* Job / project / school header — h3 simple ou table 2 cols */
h3 {
  font-size: 11pt;
  font-weight: 700;
  margin: 7pt 0 1pt;
}

/* Table 2 cols pour "Company || Location" et "**Title** || *Date*" */
table.row {
  width: 100%;
  margin: 0 0 1pt;
  border: 0;
}
table.row td.left {
  text-align: left;
  vertical-align: top;
}
table.row td.right {
  text-align: right;
  vertical-align: top;
}
table.row td.bold { font-weight: 700; }
table.row td.italic { font-style: italic; }

/* Description courte sous l'en-tête de job (italic, gris discret) */
.desc {
  font-size: 9.5pt;
  color: #555;
  font-style: italic;
  margin: 0 0 1pt;
}

p { margin: 2pt 0; }
ul {
  padding-left: 16pt;
  margin: 3pt 0 4pt;
}
li {
  margin-bottom: 1pt;
}
strong { font-weight: 700; }
em { font-style: italic; }
"""


def _inline_md(text: str) -> str:
    """Inline markdown : **bold** -> <strong>, *italic* -> <em>."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^\*]+?)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _strip_outer_bold(text: str) -> str:
    m = re.match(r"^\*\*(.+?)\*\*$", text)
    return _inline_md(m.group(1) if m else text)


def _strip_outer_emph(text: str) -> str:
    m = re.match(r"^\*(.+?)\*$", text)
    return _inline_md(m.group(1) if m else text)


def _md_to_html(md: str) -> str:
    """Convertit notre Markdown 'tailoré CV' en HTML avec layout 2-colonnes.

    Convention :
      # Nom                              -> <h1>
      Ligne 2                            -> tagline centrée
      Ligne 3                            -> contact centré
      ## SECTION                         -> <h2> souligné
      ### Company || Location            -> table 2 cols (gauche bold)
      **Job Title** || *Date range*      -> table 2 cols (gauche bold, droite italic)
      - bullet                           -> <li> dans <ul>
      texte                              -> <p>
    """
    lines = md.split("\n")
    out: list[str] = []
    in_list = False
    in_header_region = False
    header_lines_taken = 0  # 0 = tagline, 1+ = contact

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        s = raw.strip()

        if not s:
            close_list()
            continue

        if s.startswith("# "):
            close_list()
            out.append(f"<h1>{_inline_md(s[2:].strip())}</h1>")
            in_header_region = True
            header_lines_taken = 0
            continue

        if s.startswith("## "):
            close_list()
            in_header_region = False
            out.append(f"<h2>{_inline_md(s[3:].strip())}</h2>")
            continue

        if s.startswith("### "):
            close_list()
            in_header_region = False
            content = s[4:].strip()
            if "||" in content:
                left, right = (x.strip() for x in content.split("||", 1))
                out.append(
                    '<table class="row"><tr>'
                    f'<td class="left bold">{_inline_md(left)}</td>'
                    f'<td class="right">{_inline_md(right)}</td>'
                    "</tr></table>"
                )
            else:
                out.append(f"<h3>{_inline_md(content)}</h3>")
            continue

        if s.startswith("- "):
            in_header_region = False
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(s[2:])}</li>")
            continue

        if s.startswith("**") and "||" in s:
            close_list()
            left, right = (x.strip() for x in s.split("||", 1))
            out.append(
                '<table class="row"><tr>'
                f'<td class="left bold">{_strip_outer_bold(left)}</td>'
                f'<td class="right italic">{_strip_outer_emph(right)}</td>'
                "</tr></table>"
            )
            continue

        if in_header_region:
            close_list()
            cls = "tagline" if header_lines_taken == 0 else "contact"
            out.append(f'<p class="{cls}">{_inline_md(s)}</p>')
            header_lines_taken += 1
            continue

        close_list()
        out.append(f"<p>{_inline_md(s)}</p>")

    close_list()
    return "\n".join(out)


def render_pdf(md_content: str, output_path: Path) -> Path:
    """Convertit le Markdown en PDF via xhtml2pdf et l'écrit à output_path.

    xhtml2pdf est 100% Python (reportlab + html5lib en interne) et n'a
    aucune dépendance système — contrairement à WeasyPrint qui demande
    GTK/Pango/Cairo, problématique sous Windows. On évite la lib
    `markdown` car xhtml2pdf ne supporte pas le flex ; on émet
    directement des <table> 2-colonnes pour le layout pro.
    """
    from xhtml2pdf import pisa

    html_body = _md_to_html(md_content)
    html_full = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_PDF_CSS}</style></head>"
        f"<body>{html_body}</body></html>"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        result = pisa.CreatePDF(src=html_full, dest=f, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"xhtml2pdf a échoué ({result.err} erreur(s) de rendu)")
    return output_path


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────


def tailor_cv(offer: dict[str, Any]) -> dict[str, Any]:
    """Adapte le CV de base à l'offre, génère un PDF, retourne les métadonnées.

    Args:
        offer: champs structurés de l'offre (title, company, location, skills, etc.)

    Returns:
        {
            "saved_path": str,         # chemin absolu du PDF généré
            "filename": str,           # nom du fichier seul
            "folder": str,             # dossier entreprise
            "markdown": str,           # contenu MD pour debug/preview
        }

    Raises:
        FileNotFoundError: profil ou base_cv_path absent
        ValueError: cv_output_dir manquant ou DOCX invalide
        RuntimeError: clé API absente ou Gemini KO
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY non configurée")

    profile = load_profile()
    base_path = profile.get("base_cv_path", "").strip()
    if not base_path:
        raise ValueError("profile.base_cv_path n'est pas configuré (DOCX source manquant)")

    base_text = read_base_cv(base_path)
    if not base_text:
        raise ValueError("Le DOCX est vide ou illisible")

    output_path = resolve_output_path(profile, offer)

    # Optional tailored summary (default on). Failure is non-blocking.
    include_summary = profile.get("include_summary", True)
    summary: str | None = None
    if include_summary:
        summary = generate_summary(offer, profile, base_text)
        logger.info(
            "cv_tailor: summary=%s",
            "ok" if summary else "skipped",
        )

    prompt = _build_prompt(base_text, offer, profile)
    md_raw = _call_gemini(prompt)
    md_content = _strip_md_fences(md_raw)
    if not md_content.strip():
        raise RuntimeError("Gemini a renvoyé un CV vide")

    if summary:
        md_content = _inject_summary(md_content, summary)

    render_pdf(md_content, output_path)

    logger.info("cv_tailor: PDF généré -> %s", output_path)
    return {
        "saved_path": str(output_path),
        "filename": output_path.name,
        "folder": str(output_path.parent),
        "markdown": md_content,
        "summary_used": bool(summary),
    }

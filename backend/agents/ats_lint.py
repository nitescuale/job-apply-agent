"""ats_lint — analyse déterministe d'un CV PDF pour la conformité ATS.

Pas de LLM ici. Le score et les suggestions sont calculés à partir de
règles dures lisibles : parsabilité du texte, couverture des mots-clés
de l'offre, présence des sections attendues, longueur plausible (1-2
pages), bloc contact (email + téléphone).

API publique :
    lint_cv(pdf_path, offer) -> {ats_score: 0-100, checks: [...], suggestions: [...]}

Pondération du score (somme = 100) :
    parsability        : 30  (binaire — un PDF image-only est mort à l'ATS)
    keyword_coverage   : 30  (proportionnel — coverage_ratio * 30)
    section_experience :  8  (binaire)
    section_education  :  8  (binaire)
    section_skills     :  6  (binaire)
    length             :  8  (binaire — 1 ou 2 pages)
    contact_block      : 10  (binaire — email + téléphone)
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Normalisation & matching de skills
# ──────────────────────────────────────────────────────────────────────────


def _normalize(s: Any) -> str:
    """NFKD + drop accents + lowercase + collapse whitespace.

    Aligné avec match_scorer pour que "L'Oréal" / "L'Oreal" / "PYTHON" /
    "python" tombent sur la même clé. Utile pour les comparaisons de
    skills entre l'offre et le texte du CV.
    """
    s = unicodedata.normalize("NFKD", str(s))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _skill_is_present(skill: str, text_norm: str) -> bool:
    """Cherche un skill dans le texte normalisé.

    Utilise un match avec word-boundary (\\b) pour éviter qu'un skill court
    comme "R" ou "Go" matche dans un mot quelconque ("our", "going"). Les
    skills multi-mots ("Machine Learning") sont matchés comme une sous-
    chaîne exacte (les espaces sont déjà normalisés).
    """
    n = _normalize(skill)
    if not n:
        return False
    # Si le skill contient un espace, on cherche une sous-chaîne simple
    # (un word boundary autour d'une expression à espaces est ambigu).
    if " " in n:
        return n in text_norm
    # Sinon — un seul token — on encadre avec word-boundaries.
    pattern = r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])"
    return re.search(pattern, text_norm) is not None


# ──────────────────────────────────────────────────────────────────────────
# Détection de sections
# ──────────────────────────────────────────────────────────────────────────


# Mapping clé canonique -> mots-clés acceptés en en-tête (FR + EN). On
# matche en MAJUSCULES (les en-têtes de CV sont quasi toujours en caps).
_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "experience": (
        "EXPERIENCE",
        "EXPÉRIENCE",
        "EXPERIENCES",
        "EXPÉRIENCES",
        "WORK EXPERIENCE",
        "PROFESSIONAL EXPERIENCE",
        "EMPLOYMENT",
        "EMPLOYMENT HISTORY",
        "WORK HISTORY",
        "PARCOURS",
        "PARCOURS PROFESSIONNEL",
    ),
    "education": (
        "EDUCATION",
        "ÉDUCATION",
        "FORMATION",
        "FORMATIONS",
        "ACADEMIC",
        "ACADEMIC BACKGROUND",
        "DIPLOMAS",
        "DIPLÔMES",
        "ETUDES",
        "ÉTUDES",
    ),
    "skills": (
        "SKILLS",
        "TECHNICAL SKILLS",
        "TECH STACK",
        "COMPETENCES",
        "COMPÉTENCES",
        "COMPETENCES TECHNIQUES",
        "COMPÉTENCES TECHNIQUES",
        "SAVOIR-FAIRE",
    ),
}


def _detect_sections(text: str) -> set[str]:
    """Renvoie l'ensemble des sections trouvées dans le CV.

    Stratégie : on scanne ligne par ligne, on retient les lignes qui
    ressemblent à un en-tête (court ET majoritairement en majuscules, ou
    correspondance exacte à un mot-clé connu). Tolérant aux accents : on
    compare en NFKD-ASCII pour matcher "ÉDUCATION" via "EDUCATION".
    """
    found: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) > 60:
            continue
        # Comparaison ASCII-only en upper pour la robustesse aux accents
        ascii_upper = (
            unicodedata.normalize("NFKD", line)
            .encode("ascii", "ignore")
            .decode("ascii")
            .upper()
        )
        if not ascii_upper:
            continue
        # On compare aux keywords (eux-mêmes ASCII-upper côté constante)
        for canonical, keywords in _SECTION_KEYWORDS.items():
            for kw in keywords:
                kw_ascii = (
                    unicodedata.normalize("NFKD", kw)
                    .encode("ascii", "ignore")
                    .decode("ascii")
                    .upper()
                )
                # Match exact OU "EXPÉRIENCE PROFESSIONNELLE" qui contient
                # "EXPERIENCE" → on accepte les en-têtes plus longs tant
                # que le mot-clé apparaît tel quel.
                if kw_ascii == ascii_upper or (
                    kw_ascii in ascii_upper and len(ascii_upper) <= 50
                ):
                    found.add(canonical)
                    break
    return found


# ──────────────────────────────────────────────────────────────────────────
# Bloc contact
# ──────────────────────────────────────────────────────────────────────────


# Email : pattern volontairement permissif.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Téléphone : préfixe optionnel + au moins 8 chiffres séparés par espaces/
# tirets/points/parenthèses. On évite les pure-chiffres courts (codes
# postaux, dates) en exigeant ≥ 8 chiffres total.
_PHONE_RE = re.compile(r"(?:\+|00)?\s*\d[\d\s.() -]{7,}\d")


def _has_email(text: str) -> bool:
    return bool(_EMAIL_RE.search(text))


def _has_phone(text: str) -> bool:
    matches = _PHONE_RE.findall(text)
    for m in matches:
        # Compte les chiffres pour rejeter dates et codes courts
        digits = sum(c.isdigit() for c in m)
        if 8 <= digits <= 16:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────
# Extraction PDF
# ──────────────────────────────────────────────────────────────────────────


def _extract_pdf(pdf_path: Path) -> tuple[str, int]:
    """Retourne (texte, nb_pages).

    Isolé pour faciliter le mock en test (sans avoir à fabriquer un vrai
    PDF). Utilise pdfminer.six (pure-Python, pas de dépendance native).
    """
    from pdfminer.high_level import extract_pages, extract_text

    text = extract_text(str(pdf_path)) or ""
    # extract_pages est un générateur paresseux — on consomme pour compter
    page_count = sum(1 for _ in extract_pages(str(pdf_path)))
    return text, page_count


# ──────────────────────────────────────────────────────────────────────────
# Lint principal
# ──────────────────────────────────────────────────────────────────────────


# Seuils de "texte extractible" — sous ce nombre de caractères, on
# considère le PDF comme image-only (un CV typique fait 1500-3000 chars
# de texte utile).
_MIN_PARSABLE_CHARS = 200
_MIN_COVERAGE_PASS = 0.5  # coverage_ratio sous lequel on flag


def _build_checks(
    text: str,
    page_count: int,
    offer: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Calcule la liste de checks + listes matched/missing skills.

    Sortie : (checks, matched_skills, missing_skills).
    `checks` est une liste de dicts {name, passed, detail, weight,
    [partial_score]}. partial_score est utilisé uniquement pour le check
    `keyword_coverage` (score proportionnel à la couverture).
    """
    checks: list[dict[str, Any]] = []
    text_stripped = text.strip()

    # 1. Parsability — y a-t-il du texte extractible ?
    parsable = len(text_stripped) >= _MIN_PARSABLE_CHARS
    checks.append({
        "name": "parsability",
        "passed": parsable,
        "weight": 30,
        "detail": (
            f"{len(text_stripped)} caractères extraits"
            if parsable
            else "Texte non extractible — PDF probablement image-only (scan)."
        ),
    })

    # 2. Keyword coverage — score proportionnel
    offer_skills = [str(s) for s in (offer.get("skills") or []) if s]
    text_norm = _normalize(text)
    matched: list[str] = []
    missing: list[str] = []
    seen_norm: set[str] = set()
    for s in offer_skills:
        n = _normalize(s)
        if not n or n in seen_norm:
            continue
        seen_norm.add(n)
        if _skill_is_present(s, text_norm):
            matched.append(s)
        else:
            missing.append(s)
    total_unique = len(matched) + len(missing)
    coverage_ratio = (len(matched) / total_unique) if total_unique else 1.0
    checks.append({
        "name": "keyword_coverage",
        "passed": coverage_ratio >= _MIN_COVERAGE_PASS,
        "weight": 30,
        # partial_score : on note proportionnellement à la couverture
        "partial_score": round(coverage_ratio * 30),
        "detail": (
            f"{len(matched)}/{total_unique} compétence(s) couverte(s) "
            f"({round(coverage_ratio * 100)}%)"
            if total_unique
            else "Aucune compétence détectée dans l'offre — couverture non applicable."
        ),
    })

    # 3. Sections expérience / formation / compétences
    sections = _detect_sections(text)
    for canonical, label, weight in (
        ("experience", "expérience", 8),
        ("education", "formation", 8),
        ("skills", "compétences", 6),
    ):
        present = canonical in sections
        checks.append({
            "name": f"section_{canonical}",
            "passed": present,
            "weight": weight,
            "detail": (
                f"Section « {label} » détectée."
                if present
                else f"Section « {label} » non détectée — un ATS pourrait classer mal le contenu."
            ),
        })

    # 4. Longueur — 1 à 2 pages, conforme à la pratique
    length_ok = 1 <= page_count <= 2
    checks.append({
        "name": "length",
        "passed": length_ok,
        "weight": 8,
        "detail": (
            f"{page_count} page(s)."
            if length_ok
            else (
                f"{page_count} page(s) — hors de la fourchette 1-2 pages "
                "recommandée pour un CV ATS."
            )
        ),
    })

    # 5. Bloc contact
    email = _has_email(text)
    phone = _has_phone(text)
    contact_ok = email and phone
    if contact_ok:
        contact_detail = "Email + téléphone détectés."
    elif email and not phone:
        contact_detail = "Email détecté mais téléphone manquant."
    elif phone and not email:
        contact_detail = "Téléphone détecté mais email manquant."
    else:
        contact_detail = "Ni email ni téléphone détectés dans le CV."
    checks.append({
        "name": "contact_block",
        "passed": contact_ok,
        "weight": 10,
        "detail": contact_detail,
    })

    return checks, matched, missing


def _compute_score(checks: list[dict[str, Any]]) -> int:
    """Somme des poids des checks passés. Coverage utilise partial_score."""
    score = 0
    for c in checks:
        if c["name"] == "keyword_coverage":
            score += int(c.get("partial_score") or 0)
        elif c["passed"]:
            score += int(c.get("weight") or 0)
    return max(0, min(100, score))


def _build_suggestions(
    checks: list[dict[str, Any]],
    missing_skills: list[str],
    page_count: int,
) -> list[str]:
    """Génère des suggestions actionnables à partir des checks ratés."""
    by_name = {c["name"]: c for c in checks}
    out: list[str] = []

    if not by_name["parsability"]["passed"]:
        out.append(
            "Le PDF ne contient pas de texte extractible. Régénère depuis le "
            "DOCX source — ne scanne pas une version papier."
        )

    if not by_name["keyword_coverage"]["passed"] and missing_skills:
        sample = ", ".join(missing_skills[:5])
        more = "…" if len(missing_skills) > 5 else ""
        out.append(
            f"Couverture des compétences faible. Manquent dans le CV : "
            f"{sample}{more}. Mentionne-les explicitement si tu les possèdes."
        )

    for canonical, label in (
        ("experience", "expérience professionnelle"),
        ("education", "formation"),
        ("skills", "compétences"),
    ):
        if not by_name[f"section_{canonical}"]["passed"]:
            out.append(
                f"Ajoute (ou rends visible) une section « {label.capitalize()} » "
                f"avec un en-tête clair — c'est un signal fort pour les ATS."
            )

    if not by_name["length"]["passed"]:
        if page_count == 0:
            out.append("Aucune page lisible — vérifie l'intégrité du PDF.")
        elif page_count > 2:
            out.append(
                f"CV en {page_count} pages — vise 1 à 2 pages. Élague les "
                "missions très anciennes ou hors sujet."
            )

    if not by_name["contact_block"]["passed"]:
        out.append(
            "Ajoute email + téléphone dans le header. Sans bloc contact, "
            "un ATS peut écarter le CV même si tout le reste est bon."
        )

    return out


def lint_cv(pdf_path: str | Path, offer: dict[str, Any]) -> dict[str, Any]:
    """Lint déterministe d'un CV PDF.

    Args:
        pdf_path: chemin absolu du PDF généré par /tailor-cv.
        offer: champs structurés de l'offre (skills surtout) pour la
            couverture des mots-clés. Peut être {} — dans ce cas la
            couverture est notée 100 (rien à matcher).

    Returns:
        {
            "ats_score": int 0-100,
            "checks": [{name, passed, detail, weight, partial_score?}, ...],
            "suggestions": [str, ...],
            "matched_skills": [str, ...],
            "missing_skills": [str, ...],
            "page_count": int,
            "text_length": int,
        }

    Raises:
        FileNotFoundError: si pdf_path n'existe pas.
    """
    p = Path(pdf_path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"PDF introuvable: {p}")

    try:
        text, page_count = _extract_pdf(p)
    except Exception as exc:  # noqa: BLE001
        # Un PDF corrompu ou un format inattendu ne doit pas planter le
        # endpoint — on renvoie un rapport "parsability=False".
        logger.error("ats_lint: extraction PDF échouée — %s", exc)
        text, page_count = "", 0

    checks, matched, missing = _build_checks(text, page_count, offer)
    score = _compute_score(checks)
    suggestions = _build_suggestions(checks, missing, page_count)

    logger.info(
        "ats_lint: score=%d, pages=%d, text=%d chars, suggestions=%d",
        score,
        page_count,
        len(text),
        len(suggestions),
    )
    return {
        "ats_score": score,
        "checks": checks,
        "suggestions": suggestions,
        "matched_skills": matched,
        "missing_skills": missing,
        "page_count": page_count,
        "text_length": len(text),
    }

"""Tests pour backend/agents/ats_lint.py.

L'agent est déterministe — pas de mock LLM. On mock l'extraction PDF
(via _extract_pdf) pour ne pas avoir à fabriquer de vrais PDF en tests.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agents import ats_lint


# ──────────────────────────────────────────────────────────────────────────
# Sample CV text — utilisé par la majorité des tests
# ──────────────────────────────────────────────────────────────────────────


SAMPLE_CV_TEXT = """Alex Nitescu
Data Scientist - Paris, France
alex@example.com · +33 6 12 34 56 78 · linkedin.com/in/alex

SUMMARY
Applied mathematics graduate building production ML pipelines and
full-stack AI products.

EXPERIENCE
Exponens — Paris, France
Built end-to-end data pipelines processing 100M+ records daily using
Python, Pandas and PostgreSQL.
Designed dashboards consumed by 5+ business teams.

EDUCATION
CY Tech — Cergy, France
Relevant coursework: Advanced Machine Learning, Deep Learning, NLP.

SKILLS
Python, FastAPI, PostgreSQL, React, TypeScript, AWS.
"""


SAMPLE_OFFER = {
    "title": "Senior Data Engineer",
    "company": "BNP Paribas",
    "skills": ["Python", "PostgreSQL", "FastAPI", "Kafka", "Spark"],
}


@pytest.fixture
def fake_pdf(tmp_path):
    """Crée un fichier .pdf factice (le contenu n'est pas lu — on mock
    _extract_pdf). Permet aux checks d'existence de passer."""
    p = tmp_path / "cv.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


# ──────────────────────────────────────────────────────────────────────────
# Helpers purs : _normalize, _skill_is_present
# ──────────────────────────────────────────────────────────────────────────


def test_normalize_collapses_accents_case_whitespace():
    a = ats_lint._normalize("L'Oréal Big Data ")
    b = ats_lint._normalize("l'oreal  big   data")
    assert a == b


@pytest.mark.parametrize(
    "skill,text,expected",
    [
        ("Python", "j'utilise python tous les jours", True),
        # word-boundary : "Go" ne doit pas matcher "going"
        ("Go", "i am going to do it", False),
        ("Go", "i code in go and rust", True),
        # multi-mots : sous-chaîne
        ("Machine Learning", "machine learning is great", True),
        ("Machine Learning", "i love machinelearning", False),
        # accents tolérés des deux côtés
        ("Délégation", "delegation is key", True),
    ],
)
def test_skill_is_present_word_boundary_and_normalization(skill, text, expected):
    assert ats_lint._skill_is_present(skill, ats_lint._normalize(text)) is expected


# ──────────────────────────────────────────────────────────────────────────
# Détection de sections
# ──────────────────────────────────────────────────────────────────────────


def test_detect_sections_finds_canonical_headers_en():
    text = "EXPERIENCE\nWork stuff\n\nEDUCATION\nCY Tech\n\nSKILLS\nPython"
    found = ats_lint._detect_sections(text)
    assert found == {"experience", "education", "skills"}


def test_detect_sections_finds_french_headers_with_accents():
    text = "EXPÉRIENCE PROFESSIONNELLE\n...\n\nFORMATION\n...\n\nCOMPÉTENCES\n..."
    found = ats_lint._detect_sections(text)
    assert found == {"experience", "education", "skills"}


def test_detect_sections_accepts_aliases():
    text = "PROFESSIONAL EXPERIENCE\nx\nACADEMIC BACKGROUND\ny\nTECH STACK\nz"
    found = ats_lint._detect_sections(text)
    assert "experience" in found
    assert "education" in found
    assert "skills" in found


def test_detect_sections_empty_text_returns_empty():
    assert ats_lint._detect_sections("") == set()
    assert ats_lint._detect_sections("\n\n") == set()


def test_detect_sections_ignores_section_words_in_running_prose():
    """Une mention de "experience" dans une phrase ne doit pas suffire."""
    text = (
        "I have experience in many fields and education has been "
        "important to me, with deep skills across the board."
    )
    # Pas d'en-têtes en caps → aucune section détectée
    found = ats_lint._detect_sections(text)
    assert found == set()


# ──────────────────────────────────────────────────────────────────────────
# Bloc contact — _has_email, _has_phone
# ──────────────────────────────────────────────────────────────────────────


def test_has_email_detects_common_formats():
    assert ats_lint._has_email("contact: alex@example.com here") is True
    assert ats_lint._has_email("john.doe+tag@sub.domain.fr") is True
    assert ats_lint._has_email("no email here") is False


def test_has_phone_detects_french_format():
    assert ats_lint._has_phone("+33 6 12 34 56 78") is True
    assert ats_lint._has_phone("Tel : 06.12.34.56.78") is True
    assert ats_lint._has_phone("00 33 6 12 34 56 78") is True


def test_has_phone_rejects_short_numbers():
    """Un code postal 75001 ne doit pas matcher comme téléphone."""
    assert ats_lint._has_phone("Paris 75001") is False
    assert ats_lint._has_phone("date: 2026") is False


# ──────────────────────────────────────────────────────────────────────────
# Lint complet — _build_checks + score
# ──────────────────────────────────────────────────────────────────────────


def test_lint_cv_on_solid_sample(fake_pdf):
    """CV bien formé : tous les checks passent ou presque, score > 80."""
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 1)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    assert 80 <= report["ats_score"] <= 100
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["parsability"]["passed"] is True
    assert by_name["section_experience"]["passed"] is True
    assert by_name["section_education"]["passed"] is True
    assert by_name["section_skills"]["passed"] is True
    assert by_name["length"]["passed"] is True
    assert by_name["contact_block"]["passed"] is True
    # Couverture : 3/5 (Python, PostgreSQL, FastAPI) → ≥ 50% → passed
    assert by_name["keyword_coverage"]["passed"] is True
    assert set(report["matched_skills"]) >= {"Python", "PostgreSQL", "FastAPI"}
    assert set(report["missing_skills"]) >= {"Kafka", "Spark"}


def test_lint_cv_image_only_pdf_flags_parsability(fake_pdf):
    """Texte vide (PDF image-only) → parsability=False, score très bas."""
    with patch.object(ats_lint, "_extract_pdf", return_value=("", 1)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["parsability"]["passed"] is False
    # Avec parsability=False et aucun mot-clé matché → score très bas
    # (au max le bonus longueur 8 + un peu de coverage si offre vide)
    assert report["ats_score"] <= 20
    # Au moins une suggestion doit cibler le manque de texte extractible
    joined = " ".join(report["suggestions"]).lower()
    assert "extractible" in joined or "docx" in joined


def test_lint_cv_image_only_pdf_with_some_text_still_flags(fake_pdf):
    """Quelques caractères extraits (< 200) → toujours considéré image-only."""
    with patch.object(ats_lint, "_extract_pdf", return_value=("Alex Nitescu", 1)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["parsability"]["passed"] is False


def test_lint_cv_missing_sections_lowers_score(fake_pdf):
    """CV sans en-têtes EXPERIENCE/EDUCATION/SKILLS → suggestions correspondantes."""
    text = (
        "Alex Nitescu — Data Scientist\n"
        "alex@example.com · +33 6 12 34 56 78\n\n"
        "Built end-to-end data pipelines using Python and PostgreSQL "
        "across various teams and clients over several years.\n"
    ) * 5  # padding pour passer la parsability
    with patch.object(ats_lint, "_extract_pdf", return_value=(text, 1)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["section_experience"]["passed"] is False
    assert by_name["section_education"]["passed"] is False
    assert by_name["section_skills"]["passed"] is False
    # Suggestions : on parle bien des sections manquantes
    joined = " ".join(report["suggestions"]).lower()
    assert "expérience" in joined or "experience" in joined
    assert "formation" in joined or "education" in joined
    assert "compétences" in joined or "competences" in joined


def test_lint_cv_too_many_pages_flags_length(fake_pdf):
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 4)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["length"]["passed"] is False
    assert any("1 à 2" in s or "1-2" in s for s in report["suggestions"])


def test_lint_cv_missing_contact_flags(fake_pdf):
    """CV sans email ni téléphone → contact_block fail + suggestion."""
    text_no_contact = (
        "Alex Nitescu - Data Scientist\n\n"
        "SUMMARY\nApplied mathematics graduate building production ML "
        "pipelines and full-stack AI products at scale across multiple "
        "industries from finance to healthcare.\n\n"
        "EXPERIENCE\nBuilt data pipelines using Python and PostgreSQL.\n\n"
        "EDUCATION\nCY Tech\n\nSKILLS\nPython\n"
    )
    with patch.object(ats_lint, "_extract_pdf", return_value=(text_no_contact, 1)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["contact_block"]["passed"] is False
    assert any("email" in s.lower() or "contact" in s.lower() for s in report["suggestions"])


def test_lint_cv_no_offer_skills_treats_coverage_as_full(fake_pdf):
    """Si l'offre n'a pas de skills, on ne pénalise pas la couverture."""
    offer_no_skills = {"title": "Engineer", "company": "ACME"}
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 1)):
        report = ats_lint.lint_cv(fake_pdf, offer_no_skills)

    by_name = {c["name"]: c for c in report["checks"]}
    cov = by_name["keyword_coverage"]
    assert cov["passed"] is True
    assert cov["partial_score"] == 30  # ratio plein → poids plein


def test_lint_cv_coverage_partial_score_proportional(fake_pdf):
    """Coverage 2/5 → 40% → partial_score ≈ 12 (40% de 30)."""
    offer = {"skills": ["Python", "PostgreSQL", "Kafka", "Spark", "Hadoop"]}
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 1)):
        report = ats_lint.lint_cv(fake_pdf, offer)

    by_name = {c["name"]: c for c in report["checks"]}
    cov = by_name["keyword_coverage"]
    assert cov["partial_score"] == 12  # round(2/5 * 30)
    assert cov["passed"] is False  # < 50% → fail


def test_lint_cv_dedupes_offer_skills(fake_pdf):
    """Offre listant Python et python → un seul skill effectif."""
    offer = {"skills": ["Python", "python", "PYTHON"]}
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 1)):
        report = ats_lint.lint_cv(fake_pdf, offer)

    # 1 skill unique matché → coverage_ratio = 1.0 → partial_score = 30
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["keyword_coverage"]["partial_score"] == 30


# ──────────────────────────────────────────────────────────────────────────
# Bornes du score + extraction défensive
# ──────────────────────────────────────────────────────────────────────────


def test_lint_cv_score_always_in_bounds(fake_pdf):
    """Score toujours dans [0, 100], quel que soit le contenu."""
    # Cas extrême 1 : texte vide, offre vide
    with patch.object(ats_lint, "_extract_pdf", return_value=("", 0)):
        r1 = ats_lint.lint_cv(fake_pdf, {})
    assert 0 <= r1["ats_score"] <= 100

    # Cas extrême 2 : tout parfait
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 1)):
        r2 = ats_lint.lint_cv(fake_pdf, {"skills": ["Python"]})
    assert 0 <= r2["ats_score"] <= 100


def test_lint_cv_extraction_error_does_not_raise(fake_pdf):
    """pdfminer plante → on retombe sur (texte vide, 0 pages), pas d'exception."""
    def boom(*_a, **_kw):
        raise RuntimeError("PDF corrupted")
    with patch.object(ats_lint, "_extract_pdf", side_effect=boom):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    # On récupère un rapport, pas une exception
    assert report["ats_score"] >= 0
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["parsability"]["passed"] is False


def test_lint_cv_missing_pdf_raises():
    with pytest.raises(FileNotFoundError):
        ats_lint.lint_cv(Path("/nonexistent/cv.pdf"), {})


def test_lint_cv_output_shape(fake_pdf):
    """Toutes les clés du contrat de sortie doivent être présentes."""
    with patch.object(ats_lint, "_extract_pdf", return_value=(SAMPLE_CV_TEXT, 1)):
        report = ats_lint.lint_cv(fake_pdf, SAMPLE_OFFER)

    expected = {
        "ats_score",
        "checks",
        "suggestions",
        "matched_skills",
        "missing_skills",
        "page_count",
        "text_length",
    }
    assert set(report.keys()) == expected
    # Chaque check doit avoir au minimum {name, passed, detail, weight}
    for c in report["checks"]:
        assert {"name", "passed", "detail", "weight"} <= set(c.keys())

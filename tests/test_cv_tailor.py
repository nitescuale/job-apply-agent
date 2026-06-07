"""Tests pour backend/agents/cv_tailor.py."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agents import cv_tailor, form_filler


SAMPLE_PROFILE = {
    "first_name": "Alex",
    "last_name": "Nitescu",
    "email": "alex@example.com",
    "phone": "+33 6 12 34 56 78",
    "city": "Paris",
    "country": "France",
    "linkedin": "https://linkedin.com/in/x",
    "github": "https://github.com/x",
    "current_title": "Data Scientist",
    "years_experience": 3,
    "skills": ["Python", "FastAPI"],
    "cv_path": "",
    "base_cv_path": "",
    "cv_output_dir": "",
}

SAMPLE_OFFER = {
    "title": "Senior Data Engineer F/H",
    "company": "BNP Paribas",
    "location": "Paris, France",
    "contract_type": "CDI",
    "skills": ["Python", "Spark", "AWS"],
    "missions": ["Build pipelines", "Optimize cost"],
}


@pytest.fixture
def profile_in_tmp(tmp_path, monkeypatch):
    """Patche le PROFILE_PATH partagé avec form_filler pour pointer dans tmp_path."""
    profile_path = tmp_path / "user_profile.json"
    # Le profil de base sera complété par chaque test selon ses besoins.
    profile_path.write_text(json.dumps(SAMPLE_PROFILE), encoding="utf-8")
    monkeypatch.setattr(form_filler, "PROFILE_PATH", profile_path)
    return profile_path


def write_profile(profile_path: Path, **overrides):
    data = {**SAMPLE_PROFILE, **overrides}
    profile_path.write_text(json.dumps(data), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# slug / filename / paths
# ──────────────────────────────────────────────────────────────────────────


def test_slug_strips_accents_and_lowercases():
    assert cv_tailor._slug("L'Oréal") == "loreal"
    assert cv_tailor._slug("Société Générale") == "societe_generale"
    assert cv_tailor._slug("Welcome to the Jungle") == "welcome_to_the_jungle"
    assert cv_tailor._slug("Data/Scientist") == "data_scientist"
    assert cv_tailor._slug("  Hello   World  ") == "hello_world"


def test_slug_handles_none_and_empty():
    assert cv_tailor._slug(None) == ""
    assert cv_tailor._slug("") == ""
    assert cv_tailor._slug("---") == ""


def test_slug_allow_caps_preserves_case():
    assert cv_tailor._slug("BNP Paribas", allow_caps=True) == "BNP_Paribas"
    assert cv_tailor._slug("BNP Paribas") == "bnp_paribas"


def test_make_filename_follows_convention():
    fname = cv_tailor.make_filename(SAMPLE_PROFILE, SAMPLE_OFFER)
    assert fname == "0_cv_alex_nitescu_senior_data_engineer_f_h_bnp_paribas.pdf"


def test_make_filename_falls_back_when_fields_missing():
    profile = {"first_name": "Alex"}
    offer = {"title": "Dev"}
    fname = cv_tailor.make_filename(profile, offer)
    assert fname.startswith("0_cv_alex_x_dev_")
    assert fname.endswith(".pdf")


def test_resolve_output_path_uses_company_folder(tmp_path, profile_in_tmp):
    write_profile(profile_in_tmp, cv_output_dir=str(tmp_path / "CVs"))
    profile = form_filler.load_profile()
    path = cv_tailor.resolve_output_path(profile, SAMPLE_OFFER)
    assert path.parent.name == "BNP_Paribas"
    assert path.name.endswith(".pdf")
    assert path.parent.parent == tmp_path / "CVs"


def test_resolve_output_path_missing_dir_raises(profile_in_tmp):
    profile = form_filler.load_profile()
    with pytest.raises(ValueError, match="cv_output_dir"):
        cv_tailor.resolve_output_path(profile, SAMPLE_OFFER)


# ──────────────────────────────────────────────────────────────────────────
# DOCX reading
# ──────────────────────────────────────────────────────────────────────────


def test_read_base_cv_extracts_paragraphs(tmp_path):
    from docx import Document

    docx_path = tmp_path / "base.docx"
    doc = Document()
    doc.add_paragraph("Alex Nitescu")
    doc.add_paragraph("Data Scientist")
    doc.add_paragraph("")  # empty should be skipped
    doc.add_paragraph("Skills: Python, FastAPI")
    doc.save(str(docx_path))

    text = cv_tailor.read_base_cv(docx_path)
    assert "Alex Nitescu" in text
    assert "Data Scientist" in text
    assert "Skills: Python, FastAPI" in text


def test_read_base_cv_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cv_tailor.read_base_cv(tmp_path / "nope.docx")


def test_read_base_cv_wrong_extension_raises(tmp_path):
    bad = tmp_path / "cv.pdf"
    bad.write_bytes(b"%PDF-fake")
    with pytest.raises(ValueError, match="docx"):
        cv_tailor.read_base_cv(bad)


# ──────────────────────────────────────────────────────────────────────────
# strip fences + availability
# ──────────────────────────────────────────────────────────────────────────


def test_strip_md_fences_removes_code_fence():
    assert cv_tailor._strip_md_fences("```markdown\n# Title\n```") == "# Title"
    assert cv_tailor._strip_md_fences("```md\n## H2\n```") == "## H2"
    assert cv_tailor._strip_md_fences("```\nplain\n```") == "plain"
    assert cv_tailor._strip_md_fences("# Already clean") == "# Already clean"


def test_is_available_requires_key_profile_and_paths(profile_in_tmp, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    # base_cv_path et cv_output_dir vides → False
    assert cv_tailor.is_available() is False

    write_profile(profile_in_tmp, base_cv_path="/tmp/x.docx", cv_output_dir="/tmp/CVs")
    assert cv_tailor.is_available() is True

    monkeypatch.delenv("GEMINI_API_KEY")
    assert cv_tailor.is_available() is False


# ──────────────────────────────────────────────────────────────────────────
# Orchestration (mocked Gemini + mocked WeasyPrint)
# ──────────────────────────────────────────────────────────────────────────


def test_tailor_cv_orchestrates_end_to_end(tmp_path, profile_in_tmp, monkeypatch):
    # Prépare un DOCX source
    from docx import Document

    docx_path = tmp_path / "base.docx"
    doc = Document()
    doc.add_paragraph("Alex Nitescu — Data Scientist")
    doc.add_paragraph("Experience: 3 years in ML pipelines")
    doc.save(str(docx_path))

    out_dir = tmp_path / "CVs"
    write_profile(
        profile_in_tmp,
        base_cv_path=str(docx_path),
        cv_output_dir=str(out_dir),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    fake_md = "# Alex Nitescu\nalex@example.com\n\n## Summary\nGreat fit."

    with patch.object(cv_tailor, "_call_gemini", return_value=fake_md) as llm_mock, patch.object(
        cv_tailor, "render_pdf"
    ) as pdf_mock:
        pdf_mock.side_effect = lambda md, path: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(b"%PDF-fake"))[-1] or path
        result = cv_tailor.tailor_cv(SAMPLE_OFFER)

    assert llm_mock.called
    sent_prompt = llm_mock.call_args[0][0]
    assert "BNP Paribas" in sent_prompt
    assert "Alex Nitescu" in sent_prompt

    pdf_mock.assert_called_once()
    assert result["filename"].endswith(".pdf")
    assert "BNP_Paribas" in result["folder"]
    assert result["markdown"] == fake_md


def test_tailor_cv_requires_base_cv_path(tmp_path, profile_in_tmp, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    write_profile(profile_in_tmp, cv_output_dir=str(tmp_path / "CVs"))  # pas de base_cv_path
    with pytest.raises(ValueError, match="base_cv_path"):
        cv_tailor.tailor_cv(SAMPLE_OFFER)


def test_tailor_cv_requires_api_key(profile_in_tmp, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        cv_tailor.tailor_cv(SAMPLE_OFFER)


def test_tailor_cv_empty_llm_response_raises(tmp_path, profile_in_tmp, monkeypatch):
    from docx import Document

    docx_path = tmp_path / "base.docx"
    doc = Document()
    doc.add_paragraph("Some content")
    doc.save(str(docx_path))

    write_profile(
        profile_in_tmp,
        base_cv_path=str(docx_path),
        cv_output_dir=str(tmp_path / "CVs"),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with patch.object(cv_tailor, "_call_gemini", return_value="   "):
        with pytest.raises(RuntimeError, match="vide"):
            cv_tailor.tailor_cv(SAMPLE_OFFER)

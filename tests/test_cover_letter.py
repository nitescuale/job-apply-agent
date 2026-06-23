"""Tests pour backend/agents/cover_letter.py.

Pattern : mock `_call_gemini` + mock `convert_docx_to_pdf` (le module
cover_letter importe le converter de pdf_convert, donc on patche la
référence locale au module cover_letter).
"""
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agents import cover_letter, form_filler


SAMPLE_PROFILE = {
    "first_name": "Alex",
    "last_name": "Nitescu",
    "email": "alex@example.com",
    "phone": "+33 6 12 34 56 78",
    "city": "Paris",
    "country": "France",
    "current_title": "Data Scientist",
    "years_experience": 3,
    "education_level": "Master",
    "education_field": "Applied Mathematics",
    "school": "CY Tech",
    "skills": ["Python", "FastAPI", "PostgreSQL"],
    "languages": [
        {"name": "Français", "level": "Natif"},
        {"name": "Anglais", "level": "C1"},
    ],
    "summary": "Data scientist with 3 years building ML pipelines.",
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
    "summary": "Lead the modernization of our data platform.",
}

SAMPLE_MATCH = {
    "matched_skills": ["Python"],
    "missing_skills": ["Spark", "AWS"],
    "score": 33,
    "rationale": "Partial fit on Python only.",
}


@pytest.fixture
def profile_in_tmp(tmp_path, monkeypatch):
    """Patche le PROFILE_PATH partagé avec form_filler vers tmp_path."""
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(json.dumps(SAMPLE_PROFILE), encoding="utf-8")
    monkeypatch.setattr(form_filler, "PROFILE_PATH", profile_path)
    return profile_path


def write_profile(profile_path: Path, **overrides):
    data = {**SAMPLE_PROFILE, **overrides}
    profile_path.write_text(json.dumps(data), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# Availability + simple helpers
# ──────────────────────────────────────────────────────────────────────────


def test_is_available_requires_key_profile_and_output_dir(profile_in_tmp, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    # cv_output_dir vide → False
    assert cover_letter.is_available() is False

    write_profile(profile_in_tmp, cv_output_dir="/tmp/CVs")
    assert cover_letter.is_available() is True

    monkeypatch.delenv("GEMINI_API_KEY")
    assert cover_letter.is_available() is False


def test_make_filename_follows_convention():
    """1_Cover_Letter_Firstname_Lastname_JobTitle.pdf — préfixe 1, Title case."""
    profile = {**SAMPLE_PROFILE, "first_name": "Alexandru", "last_name": "Nitescu"}
    offer = {"title": "Deep Learning Algorithm Graduate (TikTok Search Ranking) - 2026 Start"}
    fname = cover_letter.make_filename(profile, offer)
    assert fname == "1_Cover_Letter_Alexandru_Nitescu_Deep_Learning_Algorithm_Graduate.pdf"


def test_make_filename_strips_gender_marker():
    offer = {"title": "Senior Data Engineer F/H"}
    fname = cover_letter.make_filename(SAMPLE_PROFILE, offer)
    assert fname == "1_Cover_Letter_Alex_Nitescu_Senior_Data_Engineer.pdf"


def test_make_filename_falls_back_when_missing():
    fname = cover_letter.make_filename({"first_name": "Alex"}, {"title": "Dev"})
    assert fname == "1_Cover_Letter_Alex_X_Dev.pdf"


def test_resolve_output_path_uses_company_folder(tmp_path):
    profile = {**SAMPLE_PROFILE, "cv_output_dir": str(tmp_path / "CVs")}
    path = cover_letter.resolve_output_path(profile, SAMPLE_OFFER)
    assert path.parent.name == "BNP_Paribas"
    assert path.name.startswith("1_Cover_Letter_")
    assert path.name.endswith(".pdf")
    assert path.parent.parent == tmp_path / "CVs"


def test_resolve_output_path_missing_dir_raises():
    with pytest.raises(ValueError, match="cv_output_dir"):
        cover_letter.resolve_output_path(SAMPLE_PROFILE, SAMPLE_OFFER)


# ──────────────────────────────────────────────────────────────────────────
# Prompt structure
# ──────────────────────────────────────────────────────────────────────────


def test_prompt_includes_banned_cliches_instruction():
    """Le prompt doit interdire les clichés bannis (consigne explicite)."""
    prompt = cover_letter._build_prompt(SAMPLE_OFFER, SAMPLE_PROFILE)
    # On vérifie quelques clichés clés (pas tous — la liste BANNED_CLICHES
    # peut évoluer, mais ces 3-4 doivent toujours en faire partie).
    for cliche in ("passionate", "team player", "fast learner"):
        assert cliche in prompt.lower()


def test_prompt_includes_language_rule():
    """La consigne de langue (même que l'offre) doit être explicite."""
    prompt = cover_letter._build_prompt(SAMPLE_OFFER, SAMPLE_PROFILE)
    assert "LANGUAGE RULE" in prompt
    assert "French" in prompt and "English" in prompt


def test_prompt_includes_offer_and_profile():
    prompt = cover_letter._build_prompt(SAMPLE_OFFER, SAMPLE_PROFILE)
    assert "BNP Paribas" in prompt
    assert "Senior Data Engineer" in prompt
    assert "Alex" in prompt
    assert "Python" in prompt


def test_prompt_includes_match_block_when_provided():
    prompt = cover_letter._build_prompt(SAMPLE_OFFER, SAMPLE_PROFILE, match=SAMPLE_MATCH)
    assert "--- MATCH ---" in prompt
    assert "matched_skills_emphasize_truthfully" in prompt
    assert "missing_skills_do_not_claim_present" in prompt


def test_prompt_omits_match_block_when_none():
    """Rétrocompat : sans match, pas de section `--- MATCH ---` dans le prompt
    (le mot MATCH apparaît seulement dans la description du SYSTEM)."""
    prompt = cover_letter._build_prompt(SAMPLE_OFFER, SAMPLE_PROFILE)
    assert "--- MATCH ---" not in prompt
    assert "matched_skills_emphasize_truthfully" not in prompt


def test_prompt_excludes_pii_and_unrelated_offer_fields():
    """description/url ne doivent pas alourdir le prompt."""
    offer_with_noise = {
        **SAMPLE_OFFER,
        "description": "A 10000-char description that bloats the prompt..." * 100,
        "url": "https://example.com/job/1",
        "from_cache": True,
    }
    prompt = cover_letter._build_prompt(offer_with_noise, SAMPLE_PROFILE)
    assert "10000-char description" not in prompt
    assert "https://example.com/job/1" not in prompt
    assert "from_cache" not in prompt


# ──────────────────────────────────────────────────────────────────────────
# generate_cover_letter — Gemini path mocked
# ──────────────────────────────────────────────────────────────────────────


SAMPLE_LETTER_FR = """Bonjour,

Je vous écris pour candidater au poste de Senior Data Engineer chez BNP Paribas. Mon parcours en data science (3 ans d'expérience) et mes projets ML semblent en parfaite adéquation avec vos besoins.

Cordialement,
Alex Nitescu"""

SAMPLE_LETTER_EN = """Dear hiring team,

I am writing to apply for the Senior Data Engineer position at BNP Paribas. My background in data science and my hands-on experience building Python pipelines align with your platform modernization roadmap.

Best regards,
Alex Nitescu"""


def test_generate_cover_letter_calls_gemini_and_returns_text(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch.object(cover_letter, "_call_gemini", return_value=SAMPLE_LETTER_FR) as mock:
        text = cover_letter.generate_cover_letter(SAMPLE_OFFER, SAMPLE_PROFILE)
    assert mock.called
    assert "Senior Data Engineer" in text
    assert "BNP Paribas" in text


def test_generate_cover_letter_strips_whitespace(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch.object(cover_letter, "_call_gemini", return_value="\n\n  text body \n\n"):
        text = cover_letter.generate_cover_letter(SAMPLE_OFFER, SAMPLE_PROFILE)
    assert text == "text body"


def test_generate_cover_letter_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        cover_letter.generate_cover_letter(SAMPLE_OFFER, SAMPLE_PROFILE)


def test_generate_cover_letter_raises_on_empty_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch.object(cover_letter, "_call_gemini", return_value="   \n  "):
        with pytest.raises(RuntimeError, match="vide"):
            cover_letter.generate_cover_letter(SAMPLE_OFFER, SAMPLE_PROFILE)


def test_generate_cover_letter_warns_on_banned_cliche(monkeypatch, caplog):
    """Si Gemini ignore la consigne et renvoie un cliché, on log un warning."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    sneaky = "I am a passionate engineer with a team player mindset."
    with patch.object(cover_letter, "_call_gemini", return_value=sneaky):
        with caplog.at_level(logging.WARNING, logger="backend.agents.cover_letter"):
            text = cover_letter.generate_cover_letter(SAMPLE_OFFER, SAMPLE_PROFILE)
    assert text == sneaky  # on ne raise pas, on warn seulement
    msgs = " ".join(r.message for r in caplog.records)
    assert "cliché" in msgs


def test_generate_cover_letter_forwards_match_into_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch.object(cover_letter, "_call_gemini", return_value=SAMPLE_LETTER_FR) as mock:
        cover_letter.generate_cover_letter(SAMPLE_OFFER, SAMPLE_PROFILE, match=SAMPLE_MATCH)
    sent_prompt = mock.call_args[0][0]
    assert "matched_skills_emphasize_truthfully" in sent_prompt
    assert "missing_skills_do_not_claim_present" in sent_prompt


# ──────────────────────────────────────────────────────────────────────────
# tailor_cover_letter — orchestration end-to-end
# ──────────────────────────────────────────────────────────────────────────


def test_tailor_cover_letter_writes_docx_and_calls_pdf(tmp_path, profile_in_tmp, monkeypatch):
    """Pipeline complet : Gemini text -> DOCX écrit -> convert_docx_to_pdf appelé."""
    out_dir = tmp_path / "CVs"
    write_profile(profile_in_tmp, cv_output_dir=str(out_dir))
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with patch.object(
        cover_letter, "_call_gemini", return_value=SAMPLE_LETTER_FR
    ) as llm_mock, patch.object(cover_letter, "convert_docx_to_pdf") as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        result = cover_letter.tailor_cover_letter(SAMPLE_OFFER)

    assert llm_mock.called
    pdf_mock.assert_called_once()

    # Le DOCX intermédiaire doit exister (écrit par python-docx avant la conversion)
    assert Path(result["saved_docx_path"]).is_file()
    assert result["filename"].startswith("1_Cover_Letter_")
    assert result["filename"].endswith(".pdf")
    assert "BNP_Paribas" in result["folder"]
    assert result["text"] == SAMPLE_LETTER_FR.strip()


def test_tailor_cover_letter_forwards_match(tmp_path, profile_in_tmp, monkeypatch):
    """Le match optionnel doit transiter jusqu'au prompt Gemini."""
    write_profile(profile_in_tmp, cv_output_dir=str(tmp_path / "CVs"))
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with patch.object(
        cover_letter, "_call_gemini", return_value=SAMPLE_LETTER_EN
    ) as llm_mock, patch.object(cover_letter, "convert_docx_to_pdf") as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        cover_letter.tailor_cover_letter(SAMPLE_OFFER, match=SAMPLE_MATCH)

    sent_prompt = llm_mock.call_args[0][0]
    assert "matched_skills_emphasize_truthfully" in sent_prompt


def test_tailor_cover_letter_requires_api_key(profile_in_tmp, monkeypatch):
    write_profile(profile_in_tmp, cv_output_dir="/tmp/CVs")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        cover_letter.tailor_cover_letter(SAMPLE_OFFER)


def test_tailor_cover_letter_requires_output_dir(profile_in_tmp, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    # cv_output_dir vide dans SAMPLE_PROFILE
    with pytest.raises(ValueError, match="cv_output_dir"):
        cover_letter.tailor_cover_letter(SAMPLE_OFFER)


def test_tailor_cover_letter_uses_company_subfolder(tmp_path, profile_in_tmp, monkeypatch):
    write_profile(profile_in_tmp, cv_output_dir=str(tmp_path / "CVs"))
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with patch.object(
        cover_letter, "_call_gemini", return_value=SAMPLE_LETTER_FR
    ), patch.object(cover_letter, "convert_docx_to_pdf") as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        result = cover_letter.tailor_cover_letter(SAMPLE_OFFER)

    # Dossier = {cv_output_dir}/BNP_Paribas/
    folder = Path(result["folder"])
    assert folder.name == "BNP_Paribas"
    assert folder.parent == tmp_path / "CVs"


def test_tailor_cover_letter_docx_contains_letter_body(tmp_path, profile_in_tmp, monkeypatch):
    """Le DOCX généré doit contenir le corps de la lettre (vérifié au reload)."""
    from docx import Document

    write_profile(profile_in_tmp, cv_output_dir=str(tmp_path / "CVs"))
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with patch.object(
        cover_letter, "_call_gemini", return_value=SAMPLE_LETTER_FR
    ), patch.object(cover_letter, "convert_docx_to_pdf") as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        result = cover_letter.tailor_cover_letter(SAMPLE_OFFER)

    doc = Document(result["saved_docx_path"])
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Senior Data Engineer" in full_text
    assert "BNP Paribas" in full_text
    assert "Alex Nitescu" in full_text  # nom dans le header + signature


# ──────────────────────────────────────────────────────────────────────────
# Smoke : la refactorisation pdf_convert a bien laissé _convert_docx_to_pdf
# accessible sur cv_tailor (back-compat tests).
# ──────────────────────────────────────────────────────────────────────────


def test_cv_tailor_reexport_of_convert_docx_to_pdf():
    """`cv_tailor._convert_docx_to_pdf` doit pointer sur
    `pdf_convert.convert_docx_to_pdf` (back-compat des tests qui patchent
    l'ancien nom)."""
    from backend.agents import cv_tailor, pdf_convert

    assert cv_tailor._convert_docx_to_pdf is pdf_convert.convert_docx_to_pdf

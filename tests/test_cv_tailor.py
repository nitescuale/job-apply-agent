"""Tests pour backend/agents/cv_tailor.py (pipeline DOCX-template)."""
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
# DOCX reading (legacy helper, kept for diagnostics)
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
# Editable heuristic
# ──────────────────────────────────────────────────────────────────────────


def test_is_editable_accepts_substantive_bullets():
    bullet = (
        "Built and automated end-to-end data pipelines (PostgreSQL, REST APIs, "
        "Pandas, Polars) processing 100M+ records daily"
    )
    assert cv_tailor._is_editable(bullet) is True


def test_is_editable_rejects_short_text():
    assert cv_tailor._is_editable("Alex Nitescu") is False
    assert cv_tailor._is_editable("CDI") is False
    assert cv_tailor._is_editable("") is False


def test_is_editable_rejects_all_caps_section_headers():
    assert cv_tailor._is_editable("EXPERIENCE") is False
    assert cv_tailor._is_editable("ADDITIONAL INFORMATION") is False


def test_is_editable_rejects_tab_separated_layout_lines():
    # Company tab Location and Title tab Date are layout, not content
    assert cv_tailor._is_editable("Exponens\tParis, France") is False
    assert cv_tailor._is_editable("Data Analyst Apprentice\tSeptember 2023 – Present") is False


def test_is_editable_rejects_contact_lines():
    assert cv_tailor._is_editable("Paris, France – +33 781830598 – nitescu.alex04@gmail.com") is False
    assert cv_tailor._is_editable("https://github.com/nitescuale and https://linkedin.com/in/x") is False


# ──────────────────────────────────────────────────────────────────────────
# Paragraph walking + run-preserving replacement
# ──────────────────────────────────────────────────────────────────────────


def test_collect_paragraphs_walks_body_and_tables(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Body para A")
    doc.add_paragraph("Body para B")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Cell L"
    table.cell(0, 1).text = "Cell R"

    paragraphs = cv_tailor._collect_paragraphs(doc)
    texts = [p.text for p in paragraphs]
    assert "Body para A" in texts
    assert "Body para B" in texts
    assert "Cell L" in texts
    assert "Cell R" in texts


def test_set_paragraph_text_keeps_first_run_formatting(tmp_path):
    from docx import Document

    doc = Document()
    p = doc.add_paragraph()
    r = p.add_run("Original bold text")
    r.bold = True
    p.add_run(" plain trailing")

    cv_tailor._set_paragraph_text(p, "Tailored replacement content")

    # First run carries the new text and keeps its bold styling
    assert p.runs[0].text == "Tailored replacement content"
    assert p.runs[0].bold is True
    # Other runs were emptied
    assert all(r.text == "" for r in p.runs[1:])
    # The visible paragraph text is the new content only
    assert p.text == "Tailored replacement content"


# ──────────────────────────────────────────────────────────────────────────
# _parse_edits
# ──────────────────────────────────────────────────────────────────────────


def test_parse_edits_handles_json_object():
    edits = cv_tailor._parse_edits('{"0": "new 0", "3": "new 3"}')
    assert edits == {0: "new 0", 3: "new 3"}


def test_parse_edits_strips_code_fence():
    raw = '```json\n{"5": "tailored bullet"}\n```'
    assert cv_tailor._parse_edits(raw) == {5: "tailored bullet"}


def test_parse_edits_handles_empty_response():
    assert cv_tailor._parse_edits("") == {}
    assert cv_tailor._parse_edits("   \n  ") == {}


def test_parse_edits_raises_on_invalid_json():
    with pytest.raises(RuntimeError, match="JSON invalide"):
        cv_tailor._parse_edits("not a json")


def test_parse_edits_skips_non_integer_keys():
    edits = cv_tailor._parse_edits('{"2": "ok", "title": "should be ignored"}')
    assert edits == {2: "ok"}


# ──────────────────────────────────────────────────────────────────────────
# Availability
# ──────────────────────────────────────────────────────────────────────────


def test_is_available_requires_key_profile_and_paths(profile_in_tmp, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    assert cv_tailor.is_available() is False

    write_profile(profile_in_tmp, base_cv_path="/tmp/x.docx", cv_output_dir="/tmp/CVs")
    assert cv_tailor.is_available() is True

    monkeypatch.delenv("GEMINI_API_KEY")
    assert cv_tailor.is_available() is False


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator (mocked Gemini + mocked PDF conversion)
# ──────────────────────────────────────────────────────────────────────────


def _make_docx_with_editable_bullets(path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Alex Nitescu")  # short — not editable
    doc.add_paragraph("EXPERIENCE")  # all caps — not editable
    doc.add_paragraph(
        "Built and automated end-to-end data pipelines (PostgreSQL, REST APIs, "
        "Pandas, Polars) processing 100M+ records daily"
    )  # editable
    doc.add_paragraph(
        "Designed dynamic Power BI dashboards consumed by 5+ business teams "
        "across the firm"
    )  # editable
    doc.save(str(path))


def test_tailor_cv_orchestrates_end_to_end(tmp_path, profile_in_tmp, monkeypatch):
    docx_path = tmp_path / "base.docx"
    _make_docx_with_editable_bullets(docx_path)

    out_dir = tmp_path / "CVs"
    write_profile(profile_in_tmp, base_cv_path=str(docx_path), cv_output_dir=str(out_dir))
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    # Gemini "tailored" the two editable bullets at indices 2 and 3
    fake_json = json.dumps({
        "2": "Engineered scalable Python data pipelines processing 100M+ daily records with Spark and AWS",
        "3": "Built BI dashboards consumed by 5+ teams to track revenue and cost optimization KPIs",
    })

    with patch.object(cv_tailor, "_call_gemini", return_value=fake_json) as llm_mock, patch.object(
        cv_tailor, "_convert_docx_to_pdf"
    ) as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        result = cv_tailor.tailor_cv(SAMPLE_OFFER)

    # LLM call carried the editable paragraphs and the offer
    assert llm_mock.called
    sent_prompt = llm_mock.call_args[0][0]
    assert "BNP Paribas" in sent_prompt
    assert "Built and automated end-to-end data pipelines" in sent_prompt  # original text
    assert "EXPERIENCE" not in sent_prompt.split("EDITABLE_PARAGRAPHS")[1]  # header excluded

    # PDF conversion called with our generated DOCX + target PDF
    pdf_mock.assert_called_once()
    src_arg, dst_arg = pdf_mock.call_args[0]
    assert src_arg.suffix == ".docx"
    assert dst_arg.suffix == ".pdf"

    # Result metadata
    assert result["filename"].endswith(".pdf")
    assert "BNP_Paribas" in result["folder"]
    assert result["editable_count"] == 2
    assert result["edited_count"] == 2
    assert Path(result["saved_docx_path"]).suffix == ".docx"


def test_tailor_cv_requires_base_cv_path(tmp_path, profile_in_tmp, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    write_profile(profile_in_tmp, cv_output_dir=str(tmp_path / "CVs"))
    with pytest.raises(ValueError, match="base_cv_path"):
        cv_tailor.tailor_cv(SAMPLE_OFFER)


def test_tailor_cv_requires_api_key(profile_in_tmp, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        cv_tailor.tailor_cv(SAMPLE_OFFER)


def test_tailor_cv_raises_when_no_editable_paragraphs(tmp_path, profile_in_tmp, monkeypatch):
    """DOCX qui ne contient que des en-têtes / contact / dates → rien à éditer."""
    from docx import Document

    docx_path = tmp_path / "base.docx"
    doc = Document()
    doc.add_paragraph("Alex Nitescu")
    doc.add_paragraph("EXPERIENCE")
    doc.add_paragraph("alex@example.com")
    doc.save(str(docx_path))

    write_profile(
        profile_in_tmp,
        base_cv_path=str(docx_path),
        cv_output_dir=str(tmp_path / "CVs"),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with pytest.raises(ValueError, match="éditable"):
        cv_tailor.tailor_cv(SAMPLE_OFFER)


def test_tailor_cv_tolerates_empty_edits_dict(tmp_path, profile_in_tmp, monkeypatch):
    """Si Gemini renvoie {} (rien à changer), le pipeline ne crashe pas — il
    sauvegarde une copie identique du DOCX et la convertit."""
    docx_path = tmp_path / "base.docx"
    _make_docx_with_editable_bullets(docx_path)

    write_profile(
        profile_in_tmp,
        base_cv_path=str(docx_path),
        cv_output_dir=str(tmp_path / "CVs"),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with patch.object(cv_tailor, "_call_gemini", return_value="{}"), patch.object(
        cv_tailor, "_convert_docx_to_pdf"
    ) as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        result = cv_tailor.tailor_cv(SAMPLE_OFFER)

    assert result["edited_count"] == 0
    assert result["editable_count"] == 2


def test_tailor_cv_ignores_indices_outside_editable_set(
    tmp_path, profile_in_tmp, monkeypatch
):
    """Si Gemini hallucine un index hors de la liste editable, on l'ignore
    proprement sans toucher au paragraphe inattendu."""
    docx_path = tmp_path / "base.docx"
    _make_docx_with_editable_bullets(docx_path)

    write_profile(
        profile_in_tmp,
        base_cv_path=str(docx_path),
        cv_output_dir=str(tmp_path / "CVs"),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    # index 0 ("Alex Nitescu") and 1 ("EXPERIENCE") are NOT in the editable list
    rogue = json.dumps({"0": "Hacked Name", "1": "Hacked Section", "2": "Tailored bullet"})

    with patch.object(cv_tailor, "_call_gemini", return_value=rogue), patch.object(
        cv_tailor, "_convert_docx_to_pdf"
    ) as pdf_mock:
        pdf_mock.side_effect = lambda src, dst: dst.write_bytes(b"%PDF-fake")
        result = cv_tailor.tailor_cv(SAMPLE_OFFER)

    # Only the valid editable index (2) was applied
    assert result["edited_count"] == 1

    # Re-open the saved DOCX and confirm the protected paragraphs are intact
    from docx import Document

    saved = Document(result["saved_docx_path"])
    texts = [p.text for p in saved.paragraphs]
    assert "Alex Nitescu" in texts
    assert "EXPERIENCE" in texts
    assert any("Tailored bullet" in t for t in texts)


def test_banned_cliches_constant_is_non_empty():
    assert len(cv_tailor.BANNED_CLICHES) >= 5
    assert all(isinstance(p, str) and p == p.lower() for p in cv_tailor.BANNED_CLICHES)

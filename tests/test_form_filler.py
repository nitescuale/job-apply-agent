"""Tests pour backend/agents/form_filler.py."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agents import form_filler


SAMPLE_PROFILE = {
    "first_name": "Alex",
    "last_name": "Nitescu",
    "email": "alex@example.com",
    "phone": "+33 6 12 34 56 78",
    "city": "Paris",
    "current_title": "Data Scientist",
    "years_experience": 3,
    "skills": ["Python", "FastAPI"],
    "cover_letter_template": "Hello {company}, je candidate pour {title}.",
    "cv_path": "",
}

SAMPLE_SCHEMA = {
    "formSelector": "form",
    "fields": [
        {"id": "field_0", "label": "Prénom", "type": "text", "required": True},
        {"id": "field_1", "label": "Email", "type": "email", "required": True},
        {
            "id": "field_2",
            "label": "Motivation",
            "type": "textarea",
            "required": True,
        },
    ],
}


@pytest.fixture
def profile_file(tmp_path, monkeypatch):
    """Crée un user_profile.json temporaire et patch le chemin du module."""
    path = tmp_path / "user_profile.json"
    path.write_text(json.dumps(SAMPLE_PROFILE), encoding="utf-8")
    monkeypatch.setattr(form_filler, "PROFILE_PATH", path)
    return path


def test_is_available_requires_key_and_profile(profile_file, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert form_filler.is_available() is False
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    assert form_filler.is_available() is True


def test_load_profile_returns_dict(profile_file):
    profile = form_filler.load_profile()
    assert profile["first_name"] == "Alex"
    assert profile["email"] == "alex@example.com"


def test_load_profile_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(form_filler, "PROFILE_PATH", tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError):
        form_filler.load_profile()


def test_fill_form_calls_gemini_and_returns_values(profile_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    fake_response = json.dumps({
        "field_0": "Alex",
        "field_1": "alex@example.com",
        "field_2": "Hello ACME, je candidate pour Data Engineer.",
    })

    with patch.object(form_filler, "_call_gemini", return_value=fake_response) as mock:
        result = form_filler.fill_form(
            SAMPLE_SCHEMA,
            context={"title": "Data Engineer", "company": "ACME"},
        )

    assert mock.called
    # Vérifie que le profil ET le contexte ont été inclus dans le prompt
    sent_prompt = mock.call_args[0][0]
    assert "ACME" in sent_prompt
    assert "Alex" in sent_prompt

    assert result["values"]["field_0"] == "Alex"
    assert result["values"]["field_1"] == "alex@example.com"
    assert "ACME" in result["values"]["field_2"]
    assert result["cv_base64"] is None  # cv_path vide


def test_fill_form_handles_markdown_fences(profile_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    fenced = '```json\n{"field_0": "Alex"}\n```'
    with patch.object(form_filler, "_call_gemini", return_value=fenced):
        result = form_filler.fill_form(SAMPLE_SCHEMA)
    assert result["values"] == {"field_0": "Alex"}


def test_fill_form_invalid_json_raises(profile_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch.object(form_filler, "_call_gemini", return_value="not json at all"):
        with pytest.raises(RuntimeError, match="JSON invalide"):
            form_filler.fill_form(SAMPLE_SCHEMA)


def test_fill_form_non_object_raises(profile_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch.object(form_filler, "_call_gemini", return_value="[1, 2, 3]"):
        with pytest.raises(RuntimeError, match="objet"):
            form_filler.fill_form(SAMPLE_SCHEMA)


def test_fill_form_no_api_key_raises(profile_file, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        form_filler.fill_form(SAMPLE_SCHEMA)


def test_get_cv_base64_returns_none_when_no_path(profile_file):
    assert form_filler.get_cv_base64() is None


def test_system_prompt_mentions_qa_bank():
    """Le _SYSTEM doit instruire Gemini sur l'usage de la qa_bank — sans ça,
    le LLM régénère tout à froid au lieu d'adapter les réponses canoniques."""
    assert "qa_bank" in form_filler._SYSTEM.lower()
    # Quelques clés canoniques mentionnées par exemple
    assert "availability" in form_filler._SYSTEM
    assert "salary_expectations" in form_filler._SYSTEM


def test_fill_form_passes_qa_bank_into_prompt(profile_file, monkeypatch):
    """Si la qa_bank est dans le profil, ses entrées atterrissent dans le prompt
    Gemini (sérialisation JSON complète du profil)."""
    qa_bank = {
        "availability": "Disponible juin 2026.",
        "salary_expectations": "45-55k EUR/an.",
        "visa_sponsorship": "Citoyen UE.",
    }
    profile = {**SAMPLE_PROFILE, "qa_bank": qa_bank}
    profile_file.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    schema = {
        "fields": [
            {"id": "field_av", "label": "Quand êtes-vous disponible ?", "type": "text"},
            {"id": "field_sal", "label": "Prétentions salariales", "type": "text"},
        ]
    }
    fake = json.dumps({
        "field_av": "Disponible juin 2026 (fin de mes études).",
        "field_sal": "45-55k EUR brut annuel.",
    })
    with patch.object(form_filler, "_call_gemini", return_value=fake) as mock:
        form_filler.fill_form(schema, context={"title": "ML Eng", "company": "ACME"})

    sent = mock.call_args[0][0]
    # Les valeurs canoniques de la qa_bank doivent être visibles dans le prompt
    assert "Disponible juin 2026" in sent
    assert "45-55k EUR" in sent
    assert "Citoyen UE" in sent


def test_fill_form_without_qa_bank_works_as_before(profile_file, monkeypatch):
    """Rétrocompat : un profil sans qa_bank doit toujours marcher."""
    # SAMPLE_PROFILE n'a pas de qa_bank → on confirme que rien ne casse.
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    fake = json.dumps({"field_0": "Alex"})
    with patch.object(form_filler, "_call_gemini", return_value=fake):
        result = form_filler.fill_form(SAMPLE_SCHEMA)
    assert result["values"] == {"field_0": "Alex"}


def test_get_cv_base64_reads_existing_file(tmp_path, monkeypatch):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-fake-content")
    profile = {**SAMPLE_PROFILE, "cv_path": str(cv)}
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(form_filler, "PROFILE_PATH", profile_path)

    encoded = form_filler.get_cv_base64()
    assert encoded is not None
    import base64
    assert base64.b64decode(encoded) == b"%PDF-fake-content"

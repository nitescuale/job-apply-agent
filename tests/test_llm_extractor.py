"""Tests pour backend/agents/llm_extractor.py — l'appel Gemini est mocké."""
import json
from unittest.mock import patch

import pytest

from backend.agents import llm_extractor

SCRAPED = {
    "url": "https://x.com/job/1",
    "title": "Data Scientist F/H",
    "company": "ACME",
    "description": "Menu Accueil Connexion ... ACME recherche un Data Scientist. "
    "Vous maîtrisez Python et SQL. Missions : construire des modèles ML. ... Footer cookies",
}

GEMINI_JSON = {
    "title": "Data Scientist F/H",
    "company": "ACME",
    "location": "Paris",
    "contract_type": "CDI",
    "salary": None,
    "remote": "Hybride",
    "experience_level": "3 ans",
    "skills": ["Python", "SQL", "Machine Learning"],
    "missions": ["Construire des modèles ML", "Analyser les données"],
    "summary": "ACME recherche un Data Scientist pour son équipe.",
}


def test_is_available_reflects_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm_extractor.is_available() is False
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    assert llm_extractor.is_available() is True


def test_extract_essentials_returns_structured_fields(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    with patch.object(llm_extractor, "_call_gemini", return_value=json.dumps(GEMINI_JSON)):
        out = llm_extractor.extract_essentials(SCRAPED)
    assert out["title"] == "Data Scientist F/H"
    assert out["skills"] == ["Python", "SQL", "Machine Learning"]
    assert len(out["missions"]) == 2
    assert out["summary"]


def test_extract_essentials_strips_markdown_fences(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    fenced = "```json\n" + json.dumps(GEMINI_JSON) + "\n```"
    with patch.object(llm_extractor, "_call_gemini", return_value=fenced):
        out = llm_extractor.extract_essentials(SCRAPED)
    assert out["company"] == "ACME"


def test_extract_essentials_raises_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm_extractor.extract_essentials(SCRAPED)


def test_extract_essentials_raises_on_invalid_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    with patch.object(llm_extractor, "_call_gemini", return_value="pas du json"):
        with pytest.raises(RuntimeError, match="JSON invalide"):
            llm_extractor.extract_essentials(SCRAPED)


def test_extract_essentials_raises_when_not_object(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    with patch.object(llm_extractor, "_call_gemini", return_value='["a", "b"]'):
        with pytest.raises(RuntimeError, match="n'est pas un objet"):
            llm_extractor.extract_essentials(SCRAPED)


def test_payload_is_truncated(monkeypatch):
    """Un scraping énorme ne doit pas exploser le prompt envoyé au LLM."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    huge = {"description": "x" * 100000}
    captured = {}

    def fake_call(prompt: str) -> str:
        captured["len"] = len(prompt)
        return json.dumps(GEMINI_JSON)

    with patch.object(llm_extractor, "_call_gemini", side_effect=fake_call):
        llm_extractor.extract_essentials(huge)

    # prompt = system + payload tronqué ; doit rester borné
    assert captured["len"] < llm_extractor.MAX_PAYLOAD_CHARS + len(llm_extractor._SYSTEM) + 100

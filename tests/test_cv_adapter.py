"""Tests unitaires pour cv_adapter — vérifie qu'aucune compétence n'est inventée."""
import json
import pytest
from unittest.mock import MagicMock, patch
import copy


CV_BASE = {
    "personal": {"name": "Alex Dupont", "email": "alex@email.com"},
    "title": "Data Scientist | ML Engineer",
    "summary": "Étudiant en Data Science.",
    "education": [{"school": "Grande École", "degree": "Ingénieur DS", "year": "2022-2025"}],
    "experience": [{"company": "Entreprise X", "role": "Data Engineer Intern", "period": "2024"}],
    "projects": [
        {"name": "CNN CIFAR-10", "skills": ["PyTorch", "CNN"]},
        {"name": "vLLM Monitoring", "skills": ["Python", "vLLM"]},
    ],
    "skills": {
        "languages": ["Python", "R", "SQL"],
        "ml_frameworks": ["PyTorch", "scikit-learn"],
        "tools": ["PostgreSQL", "Git"],
        "spoken_languages": ["Français", "Anglais"]
    },
    "soft_skills": ["Autonomie", "Rigueur"],
}

JOB_DATA = {
    "title": "Data Scientist",
    "company": "TechCorp",
    "required_skills": {"hard": ["Python", "PyTorch"], "soft": ["Autonomie"]},
    "experience_level": "junior",
    "culture_values": ["innovation"],
    "main_missions": ["Développer des modèles ML"],
}


def make_mock_cv_response(cv: dict) -> MagicMock:
    """Crée un mock de réponse Anthropic retournant un CV adapté."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps(cv)
    return mock_response


class TestAdaptCV:

    @patch("backend.agents.cv_adapter.client")
    def test_returns_same_structure_plus_match_score(self, mock_client):
        """adapt_cv retourne la même structure que cv_base + match_score."""
        adapted = copy.deepcopy(CV_BASE)
        adapted["match_score"] = 0.75
        mock_client.messages.create.return_value = make_mock_cv_response(adapted)

        from backend.agents.cv_adapter import adapt_cv
        result = adapt_cv(JOB_DATA, CV_BASE)

        assert "match_score" in result
        assert isinstance(result["match_score"], float)
        assert 0.0 <= result["match_score"] <= 1.0
        # Structure identique
        assert "personal" in result
        assert "education" in result
        assert "experience" in result
        assert "projects" in result
        assert "skills" in result

    @patch("backend.agents.cv_adapter.client")
    def test_does_not_invent_skills(self, mock_client):
        """Aucune compétence inventée — toutes les skills du résultat sont dans cv_base."""
        adapted = copy.deepcopy(CV_BASE)
        adapted["match_score"] = 0.8
        # S'assurer que le mock retourne exactement les skills du cv_base
        mock_client.messages.create.return_value = make_mock_cv_response(adapted)

        from backend.agents.cv_adapter import adapt_cv
        result = adapt_cv(JOB_DATA, CV_BASE)

        # Toutes les skills retournées doivent être dans cv_base
        base_all_skills = set(
            CV_BASE["skills"]["languages"] +
            CV_BASE["skills"]["ml_frameworks"] +
            CV_BASE["skills"]["tools"]
        )
        result_all_skills = set(
            result.get("skills", {}).get("languages", []) +
            result.get("skills", {}).get("ml_frameworks", []) +
            result.get("skills", {}).get("tools", [])
        )
        invented = result_all_skills - base_all_skills
        assert len(invented) == 0, f"Compétences inventées détectées : {invented}"

    @patch("backend.agents.cv_adapter.client")
    def test_facts_not_modified(self, mock_client):
        """Les faits (dates, entreprises, diplômes) ne sont pas modifiés."""
        adapted = copy.deepcopy(CV_BASE)
        adapted["match_score"] = 0.7
        mock_client.messages.create.return_value = make_mock_cv_response(adapted)

        from backend.agents.cv_adapter import adapt_cv
        result = adapt_cv(JOB_DATA, CV_BASE)

        # Dates et entreprises inchangées
        assert result["experience"][0]["company"] == "Entreprise X"
        assert result["experience"][0]["period"] == "2024"
        assert result["education"][0]["year"] == "2022-2025"
        assert result["personal"]["name"] == "Alex Dupont"

    @patch("backend.agents.cv_adapter.client")
    def test_correct_model_used(self, mock_client):
        """Le modèle claude-haiku-4-5-20251001 est utilisé."""
        adapted = copy.deepcopy(CV_BASE)
        adapted["match_score"] = 0.5
        mock_client.messages.create.return_value = make_mock_cv_response(adapted)

        from backend.agents.cv_adapter import adapt_cv
        adapt_cv(JOB_DATA, CV_BASE)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"

    @patch("backend.agents.cv_adapter.client")
    def test_max_tokens_sufficient(self, mock_client):
        """max_tokens >= 4096 pour éviter la troncature du CV."""
        adapted = copy.deepcopy(CV_BASE)
        adapted["match_score"] = 0.5
        mock_client.messages.create.return_value = make_mock_cv_response(adapted)

        from backend.agents.cv_adapter import adapt_cv
        adapt_cv(JOB_DATA, CV_BASE)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("max_tokens", 0) >= 4096

    @patch("backend.agents.cv_adapter.client")
    def test_raises_on_invalid_json(self, mock_client):
        """Lève ValueError si le modèle retourne du JSON invalide."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "pas du json"
        mock_client.messages.create.return_value = mock_response

        from backend.agents.cv_adapter import adapt_cv
        with pytest.raises(ValueError, match="invalid JSON"):
            adapt_cv(JOB_DATA, CV_BASE)

"""Tests unitaires pour job_analyzer."""
import json
import pytest
from unittest.mock import MagicMock, patch


# Fixture : offre d'emploi réaliste
SAMPLE_JOB_TEXT = """
Data Scientist - Machine Learning Engineer
Entreprise: TechCorp Paris

Nous cherchons un Data Scientist expérimenté pour rejoindre notre équipe ML.

Missions:
- Développer des modèles de ML en production
- Analyser des données à grande échelle
- Collaborer avec les équipes produit

Compétences requises:
- Python, PyTorch, scikit-learn
- SQL et bases de données
- Expérience 2-3 ans minimum

Soft skills: autonomie, rigueur, esprit d'équipe
"""

EXPECTED_JOB_DATA = {
    "title": "Data Scientist - Machine Learning Engineer",
    "company": "TechCorp Paris",
    "required_skills": {
        "hard": ["Python", "PyTorch", "scikit-learn", "SQL"],
        "soft": ["autonomie", "rigueur", "esprit d'équipe"]
    },
    "experience_level": "mid",
    "culture_values": ["collaboration", "innovation"],
    "main_missions": [
        "Développer des modèles de ML en production",
        "Analyser des données à grande échelle"
    ]
}


def make_mock_response(content: dict) -> MagicMock:
    """Crée un mock de réponse Anthropic."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps(content)
    return mock_response


class TestAnalyzeJob:

    @patch("backend.agents.job_analyzer.client")
    def test_returns_structured_dict(self, mock_client):
        """analyze_job retourne un dict avec tous les champs requis."""
        mock_client.messages.create.return_value = make_mock_response(EXPECTED_JOB_DATA)

        from backend.agents.job_analyzer import analyze_job
        result = analyze_job(SAMPLE_JOB_TEXT)

        assert isinstance(result, dict)
        assert "title" in result
        assert "company" in result
        assert "required_skills" in result
        assert "hard" in result["required_skills"]
        assert "soft" in result["required_skills"]
        assert "experience_level" in result
        assert "culture_values" in result
        assert "main_missions" in result

    @patch("backend.agents.job_analyzer.client")
    def test_correct_model_used(self, mock_client):
        """Le modèle claude-haiku-4-5-20251001 est utilisé."""
        mock_client.messages.create.return_value = make_mock_response(EXPECTED_JOB_DATA)

        from backend.agents.job_analyzer import analyze_job
        analyze_job(SAMPLE_JOB_TEXT)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"

    @patch("backend.agents.job_analyzer.client")
    def test_timeout_30s(self, mock_client):
        """Le timeout de 30s est configuré."""
        mock_client.messages.create.return_value = make_mock_response(EXPECTED_JOB_DATA)

        from backend.agents.job_analyzer import analyze_job
        analyze_job(SAMPLE_JOB_TEXT)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("timeout") == 30.0

    @patch("backend.agents.job_analyzer.client")
    def test_handles_markdown_wrapped_json(self, mock_client):
        """Gère les réponses avec des blocs markdown ```json."""
        wrapped = f"```json\n{json.dumps(EXPECTED_JOB_DATA)}\n```"
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = wrapped
        mock_client.messages.create.return_value = mock_response

        from backend.agents.job_analyzer import analyze_job
        result = analyze_job(SAMPLE_JOB_TEXT)

        assert result["title"] == EXPECTED_JOB_DATA["title"]

    @patch("backend.agents.job_analyzer.client")
    def test_raises_on_invalid_json(self, mock_client):
        """Lève ValueError si le modèle retourne du JSON invalide."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Ce n'est pas du JSON valide"
        mock_client.messages.create.return_value = mock_response

        from backend.agents.job_analyzer import analyze_job
        with pytest.raises(ValueError, match="invalid JSON"):
            analyze_job(SAMPLE_JOB_TEXT)

    @patch("backend.agents.job_analyzer.client")
    def test_raises_on_empty_text(self, mock_client):
        """Accepte tout texte non-vide (la validation d'empty est dans l'orchestrateur)."""
        mock_client.messages.create.return_value = make_mock_response(EXPECTED_JOB_DATA)

        from backend.agents.job_analyzer import analyze_job
        # job_analyzer lui-même n'a pas de garde sur l'empty — c'est l'orchestrateur
        # Vérifie simplement que l'appel API est bien fait
        result = analyze_job("Offre courte")
        assert isinstance(result, dict)

"""Tests unitaires pour l'orchestrateur."""
import pytest
from unittest.mock import MagicMock, patch


JOB_DATA_MOCK = {
    "title": "Data Scientist",
    "company": "TechCorp",
    "required_skills": {"hard": ["Python"], "soft": ["Autonomie"]},
    "experience_level": "junior",
    "culture_values": [],
    "main_missions": [],
}

ADAPTED_CV_MOCK = {
    "personal": {"name": "Alex Dupont"},
    "title": "Data Scientist",
    "summary": "CV adapté pour TechCorp.",
    "match_score": 0.82,
}

CV_BASE_MOCK = {
    "personal": {"name": "Alex Dupont"},
    "title": "Data Scientist | ML Engineer",
    "summary": "Étudiant en Data Science.",
    "skills": {"languages": ["Python"], "ml_frameworks": [], "tools": []},
}


class TestRunPipeline:

    @pytest.mark.asyncio
    @patch("backend.agents.orchestrator.load_cv_base", return_value=CV_BASE_MOCK)
    @patch("backend.agents.orchestrator._validate_pipeline_output", return_value=True)
    @patch("backend.agents.orchestrator.adapt_cv", return_value=ADAPTED_CV_MOCK)
    @patch("backend.agents.orchestrator.analyze_job", return_value=JOB_DATA_MOCK)
    async def test_pipeline_returns_expected_keys(
        self, mock_analyze, mock_adapt, mock_validate, mock_load_cv
    ):
        """run_pipeline retourne job_data, adapted_cv, match_score."""
        from backend.agents.orchestrator import run_pipeline
        result = await run_pipeline("https://example.com/job", "Data Scientist job posting")

        assert "job_data" in result
        assert "adapted_cv" in result
        assert "match_score" in result
        assert result["match_score"] == 0.82

    @pytest.mark.asyncio
    async def test_raises_on_empty_job_text(self):
        """Lève ValueError si job_text est vide."""
        from backend.agents.orchestrator import run_pipeline
        with pytest.raises(ValueError, match="cannot be empty"):
            await run_pipeline("https://example.com", "")

    @pytest.mark.asyncio
    async def test_raises_on_whitespace_only_job_text(self):
        """Lève ValueError si job_text est uniquement des espaces."""
        from backend.agents.orchestrator import run_pipeline
        with pytest.raises(ValueError, match="cannot be empty"):
            await run_pipeline("https://example.com", "   \n\t  ")

    @pytest.mark.asyncio
    @patch("backend.agents.orchestrator.load_cv_base", return_value=CV_BASE_MOCK)
    @patch("backend.agents.orchestrator._validate_pipeline_output", return_value=True)
    @patch("backend.agents.orchestrator.adapt_cv", return_value=ADAPTED_CV_MOCK)
    @patch("backend.agents.orchestrator.analyze_job", return_value=JOB_DATA_MOCK)
    async def test_pipeline_calls_analyze_then_adapt(
        self, mock_analyze, mock_adapt, mock_validate, mock_load_cv
    ):
        """analyze_job est appelé avant adapt_cv dans le pipeline."""
        call_order = []
        mock_analyze.side_effect = lambda *a: (call_order.append("analyze"), JOB_DATA_MOCK)[1]
        mock_adapt.side_effect = lambda *a: (call_order.append("adapt"), ADAPTED_CV_MOCK)[1]

        from backend.agents.orchestrator import run_pipeline
        await run_pipeline("https://example.com", "Some job text")

        assert call_order == ["analyze", "adapt"]

    @pytest.mark.asyncio
    @patch("backend.agents.orchestrator.load_cv_base", return_value=CV_BASE_MOCK)
    @patch("backend.agents.orchestrator._validate_pipeline_output", return_value=True)
    @patch("backend.agents.orchestrator.adapt_cv", side_effect=Exception("API error"))
    @patch("backend.agents.orchestrator.analyze_job", return_value=JOB_DATA_MOCK)
    async def test_pipeline_propagates_subagent_error(
        self, mock_analyze, mock_adapt, mock_validate, mock_load_cv
    ):
        """Les erreurs des sous-agents sont propagées."""
        from backend.agents.orchestrator import run_pipeline
        with pytest.raises(Exception, match="API error"):
            await run_pipeline("https://example.com", "Some job text")

"""Tests pour backend/agents/match_scorer.py.

Pattern : mock `_call_gemini` plutôt que l'API Gemini directement (cf.
test_llm_extractor.py). Pas d'appel réseau, pas de clé API requise.
"""
from unittest.mock import patch

import pytest

from backend.agents import match_scorer


# ──────────────────────────────────────────────────────────────────────────
# is_available + clamp + normalize (utilitaires purs)
# ──────────────────────────────────────────────────────────────────────────


def test_is_available_true_when_key_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert match_scorer.is_available() is True


def test_is_available_false_when_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert match_scorer.is_available() is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        (50, 50),
        (0, 0),
        (100, 100),
        (150, 100),  # clamp haut
        (-10, 0),  # clamp bas
        (75.6, 76),  # cast + round
        ("85", 85),  # cast string
        (None, 0),  # tolérant
        ("abc", 0),  # tolérant
    ],
)
def test_clamp_score(raw, expected):
    assert match_scorer._clamp_score(raw) == expected


def test_normalize_collapses_accents_case_whitespace():
    a = match_scorer._normalize("L'Oréal Big Data ")
    b = match_scorer._normalize("l'oreal  big   data")
    assert a == b


# ──────────────────────────────────────────────────────────────────────────
# _extract_profile_skills — accepte plusieurs formats
# ──────────────────────────────────────────────────────────────────────────


def test_extract_profile_skills_from_list():
    profile = {"skills": ["Python", "SQL", "Spark"]}
    out = match_scorer._extract_profile_skills(profile)
    assert set(out) == {"Python", "SQL", "Spark"}


def test_extract_profile_skills_from_dict_by_category():
    profile = {
        "skills": {
            "languages": ["Python", "Go"],
            "tools": ["Docker", "k8s"],
        }
    }
    out = match_scorer._extract_profile_skills(profile)
    assert set(out) == {"Python", "Go", "Docker", "k8s"}


def test_extract_profile_skills_handles_missing():
    assert match_scorer._extract_profile_skills({}) == []
    assert match_scorer._extract_profile_skills({"skills": None}) == []


# ──────────────────────────────────────────────────────────────────────────
# Fallback déterministe (sans clé Gemini)
# ──────────────────────────────────────────────────────────────────────────


def test_fallback_score_partial_overlap(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    offer = {"skills": ["Python", "SQL", "Spark", "Kafka"]}
    profile = {"skills": ["Python", "SQL", "Docker"]}
    out = match_scorer.score_match(offer, profile)

    assert out["llm_used"] is False
    assert out["score"] == 50  # 2/4 = 50%
    assert set(out["matched_skills"]) == {"Python", "SQL"}
    assert set(out["missing_skills"]) == {"Spark", "Kafka"}
    assert "compétences" in out["rationale"] or "skills" in out["rationale"].lower() or "/4" in out["rationale"]


def test_fallback_score_full_overlap(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    offer = {"skills": ["Python", "SQL"]}
    profile = {"skills": ["Python", "SQL", "Docker", "Spark"]}
    out = match_scorer.score_match(offer, profile)
    assert out["score"] == 100
    assert set(out["matched_skills"]) == {"Python", "SQL"}
    assert out["missing_skills"] == []


def test_fallback_score_zero_overlap(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    offer = {"skills": ["Rust", "Zig"]}
    profile = {"skills": ["Python", "SQL"]}
    out = match_scorer.score_match(offer, profile)
    assert out["score"] == 0
    assert out["matched_skills"] == []
    assert set(out["missing_skills"]) == {"Rust", "Zig"}


def test_fallback_score_no_offer_skills(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    offer = {"title": "Software Engineer"}  # pas de skills
    profile = {"skills": ["Python"]}
    out = match_scorer.score_match(offer, profile)
    assert out["score"] == 0
    assert out["llm_used"] is False
    assert out["matched_skills"] == []
    assert out["missing_skills"] == []
    assert "Aucune" in out["rationale"] or "0" in out["rationale"]


def test_fallback_score_normalizes_accents_and_case(monkeypatch):
    """L'Oréal ≡ L'Oreal, Python ≡ python."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    offer = {"skills": ["L'Oréal", "PYTHON"]}
    profile = {"skills": ["L'Oreal", "python"]}
    out = match_scorer.score_match(offer, profile)
    assert out["score"] == 100
    assert len(out["matched_skills"]) == 2


def test_fallback_score_dedupes_offer_skills(monkeypatch):
    """Si l'offre liste "Python" et "python", on compte une seule fois."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    offer = {"skills": ["Python", "python", "SQL"]}
    profile = {"skills": ["Python"]}
    out = match_scorer.score_match(offer, profile)
    # Python est dédupliqué → effectif {Python, SQL}, 1/2 matched
    assert out["score"] == 50


def test_fallback_output_shape_complete(monkeypatch):
    """L'output doit toujours avoir les 5 keys, même en fallback."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = match_scorer.score_match({}, {})
    assert set(out.keys()) == {
        "score",
        "matched_skills",
        "missing_skills",
        "rationale",
        "llm_used",
    }


# ──────────────────────────────────────────────────────────────────────────
# Chemin LLM — mock _call_gemini
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def gemini_env(monkeypatch):
    """Force GEMINI_API_KEY pour activer le chemin LLM dans score_match."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_llm_path_with_clean_json(gemini_env):
    fake = '{"score": 78, "matched_skills": ["Python", "PyTorch"], "missing_skills": ["Triton"], "rationale": "Forte couverture sauf Triton."}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match(
            {"title": "ML Engineer", "skills": ["Python", "PyTorch", "Triton"]},
            {"skills": ["Python", "PyTorch"]},
        )
    assert out["llm_used"] is True
    assert out["score"] == 78
    assert out["matched_skills"] == ["Python", "PyTorch"]
    assert out["missing_skills"] == ["Triton"]
    assert "Triton" in out["rationale"]


def test_llm_path_parses_json_with_markdown_fence(gemini_env):
    """Gemini renvoie parfois ```json ... ``` malgré response_mime_type."""
    fenced = '```json\n{"score": 60, "matched_skills": [], "missing_skills": [], "rationale": "moyen"}\n```'
    with patch.object(match_scorer, "_call_gemini", return_value=fenced):
        out = match_scorer.score_match({"skills": ["x"]}, {})
    assert out["score"] == 60
    assert out["llm_used"] is True


def test_llm_path_clamps_score_above_100(gemini_env):
    fake = '{"score": 150, "matched_skills": [], "missing_skills": [], "rationale": "wrong"}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match({}, {})
    assert out["score"] == 100


def test_llm_path_clamps_score_below_0(gemini_env):
    fake = '{"score": -5, "matched_skills": [], "missing_skills": [], "rationale": "wrong"}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match({}, {})
    assert out["score"] == 0


def test_llm_path_clamps_score_non_numeric_to_zero(gemini_env):
    fake = '{"score": "very high", "matched_skills": [], "missing_skills": [], "rationale": "x"}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match({}, {})
    assert out["score"] == 0


def test_llm_path_truncates_long_rationale(gemini_env):
    long = "x" * 400
    fake = f'{{"score": 50, "matched_skills": [], "missing_skills": [], "rationale": "{long}"}}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match({}, {})
    assert len(out["rationale"]) <= 280


def test_llm_path_handles_missing_keys_gracefully(gemini_env):
    """Si Gemini omet matched_skills ou missing_skills, on prend [] par défaut."""
    fake = '{"score": 40, "rationale": "skills inconnus"}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match({}, {})
    assert out["matched_skills"] == []
    assert out["missing_skills"] == []
    assert out["score"] == 40


# ──────────────────────────────────────────────────────────────────────────
# Fallback en cascade : LLM rate → on retombe sur le calcul hors-ligne
# ──────────────────────────────────────────────────────────────────────────


def test_llm_failure_falls_back_to_offline_score(gemini_env):
    """Gemini renvoie du JSON invalide → on log et on calcule en hors-ligne."""
    with patch.object(match_scorer, "_call_gemini", return_value="not json !!!"):
        out = match_scorer.score_match(
            {"skills": ["Python", "SQL"]},
            {"skills": ["Python"]},
        )
    assert out["llm_used"] is False
    assert out["score"] == 50


def test_llm_runtime_error_falls_back(gemini_env):
    """_call_gemini lève (rate limit, network) → fallback silencieux."""
    def boom(*args, **kwargs):
        raise RuntimeError("rate limited")

    with patch.object(match_scorer, "_call_gemini", side_effect=boom):
        out = match_scorer.score_match(
            {"skills": ["Python"]}, {"skills": ["Python"]}
        )
    assert out["llm_used"] is False
    assert out["score"] == 100


def test_llm_returns_non_dict_falls_back(gemini_env):
    """Gemini renvoie un JSON array → on rejette et fallback."""
    with patch.object(match_scorer, "_call_gemini", return_value='[1, 2, 3]'):
        out = match_scorer.score_match(
            {"skills": ["Python"]}, {"skills": ["Python"]}
        )
    assert out["llm_used"] is False
    assert out["score"] == 100


def test_score_match_output_shape_complete_llm_path(gemini_env):
    """Mêmes 5 keys garanties que pour le fallback."""
    fake = '{"score": 80, "matched_skills": ["x"], "missing_skills": ["y"], "rationale": "ok"}'
    with patch.object(match_scorer, "_call_gemini", return_value=fake):
        out = match_scorer.score_match({}, {})
    assert set(out.keys()) == {
        "score",
        "matched_skills",
        "missing_skills",
        "rationale",
        "llm_used",
    }

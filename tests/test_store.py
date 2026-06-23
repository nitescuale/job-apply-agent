"""Tests pour backend/store.py — DB SQLite de tracking + cache scrapes."""
import importlib
import time

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Pointe DB_PATH dans tmp_path et garantit un schéma frais par test.

    Le module store est importé une fois (cache Python) mais `_db_path()`
    relit l'env à chaque appel, donc le monkeypatch suffit pour rediriger
    toutes les opérations vers une DB temporaire dédiée au test.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from backend import store as store_module

    # Force un reset complet en re-créant tout (CREATE IF NOT EXISTS sur un
    # fichier inexistant produit un schéma vierge).
    store_module.init_db()
    return store_module


# ──────────────────────────────────────────────────────────────────────────
# Init + hash
# ──────────────────────────────────────────────────────────────────────────


def test_init_db_creates_tables(store, tmp_path):
    """init_db doit créer les deux tables et être idempotent."""
    import sqlite3

    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "applications" in names
    assert "scrapes" in names

    # Idempotent — pas de double-CREATE qui foire
    store.init_db()
    store.init_db()


def test_compute_job_hash_is_deterministic(store):
    h1 = store.compute_job_hash("ML Engineer", "BNP", "Paris")
    h2 = store.compute_job_hash("ML Engineer", "BNP", "Paris")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_job_hash_normalizes_accents_case_spaces(store):
    """L'Oréal et L'Oreal hashent au même endroit, idem pour espaces / casse."""
    a = store.compute_job_hash("Data Scientist", "L'Oréal", "Paris")
    b = store.compute_job_hash("data scientist", "L'Oreal", "paris")
    c = store.compute_job_hash("  Data  Scientist  ", "L'Oreal", " Paris ")
    assert a == b == c


def test_compute_job_hash_distinguishes_distinct_jobs(store):
    a = store.compute_job_hash("ML Engineer", "BNP", "Paris")
    b = store.compute_job_hash("ML Engineer", "BNP", "Lyon")
    c = store.compute_job_hash("Data Engineer", "BNP", "Paris")
    assert a != b
    assert a != c
    assert b != c


def test_compute_job_hash_handles_none(store):
    """None doit être traité comme vide, pas planter."""
    h = store.compute_job_hash(None, "BNP", None)
    assert isinstance(h, str)
    assert len(h) == 64


# ──────────────────────────────────────────────────────────────────────────
# upsert_application — dédup et préservation du status
# ──────────────────────────────────────────────────────────────────────────


def test_upsert_creates_new_with_seen_status(store):
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, was_new = store.upsert_application(
        job_hash,
        job_url="https://example.com/job/1",
        title="ML Eng", company="Acme", location="Paris",
    )
    assert was_new is True
    assert app_id >= 1
    row = store.get_application(app_id)
    assert row["status"] == "seen"
    assert row["company"] == "Acme"
    assert row["job_url"] == "https://example.com/job/1"
    assert row["created_at"] == row["updated_at"]


def test_upsert_existing_returns_was_new_false(store):
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    id1, new1 = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    id2, new2 = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    assert new1 is True
    assert new2 is False
    assert id1 == id2  # même row, pas un nouvel id


def test_upsert_preserves_manual_status_transition(store):
    """Re-scraper une offre déjà 'applied' ne doit PAS la repasser à 'seen'."""
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    store.update_application(app_id, status="applied")
    # Re-scrape
    app_id_2, was_new = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    assert app_id == app_id_2
    assert was_new is False
    assert store.get_application(app_id)["status"] == "applied"


def test_upsert_does_not_overwrite_existing_field_with_none(store):
    """COALESCE en UPDATE : si on passe None, on garde la valeur existante."""
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    store.upsert_application(
        job_hash, title="ML Eng", company="Acme", location="Paris",
    )
    # Second appel sans location → ne doit pas wipe l'existant
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    row = store.get_application(app_id)
    assert row["location"] == "Paris"


def test_get_application_returns_none_when_missing(store):
    assert store.get_application(99999) is None


def test_get_application_by_hash(store):
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    row = store.get_application_by_hash(job_hash)
    assert row is not None
    assert row["id"] == app_id
    assert store.get_application_by_hash("nonexistent" * 8) is None


# ──────────────────────────────────────────────────────────────────────────
# update_application — PATCH partiel + validation
# ──────────────────────────────────────────────────────────────────────────


def test_update_application_patches_only_provided_fields(store):
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    # Petit délai pour s'assurer que updated_at change
    time.sleep(1.0)
    row = store.update_application(app_id, status="applied", notes="motivation strong")
    assert row["status"] == "applied"
    assert row["notes"] == "motivation strong"
    assert row["title"] == "ML Eng"  # inchangé
    assert row["updated_at"] >= row["created_at"]


def test_update_application_status_transition_full_lifecycle(store):
    """seen -> applied -> followed_up -> interview -> response_pos doit passer."""
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    for s in ("applied", "followed_up", "interview", "response_pos"):
        row = store.update_application(app_id, status=s)
        assert row["status"] == s


def test_update_application_rejects_invalid_status(store):
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    with pytest.raises(ValueError, match="status invalide"):
        store.update_application(app_id, status="ghosted")


def test_update_application_returns_none_for_missing_id(store):
    assert store.update_application(99999, status="applied") is None


def test_update_application_sets_cv_path(store):
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    row = store.update_application(app_id, cv_path="/tmp/cv.pdf")
    assert row["cv_path"] == "/tmp/cv.pdf"


def test_update_application_noop_returns_current_row(store):
    """Patch sans aucun champ retourne la ligne sans la modifier."""
    job_hash = store.compute_job_hash("ML Eng", "Acme", "Paris")
    app_id, _ = store.upsert_application(job_hash, title="ML Eng", company="Acme")
    before = store.get_application(app_id)
    row = store.update_application(app_id)
    assert row == before


# ──────────────────────────────────────────────────────────────────────────
# list_applications — filtres
# ──────────────────────────────────────────────────────────────────────────


def test_list_applications_returns_all_by_default(store):
    for i in range(3):
        store.upsert_application(
            store.compute_job_hash(f"Role {i}", "Acme", "Paris"),
            title=f"Role {i}", company="Acme",
        )
    rows = store.list_applications()
    assert len(rows) == 3


def test_list_applications_filters_by_status(store):
    h1 = store.compute_job_hash("Role 1", "Acme", "Paris")
    h2 = store.compute_job_hash("Role 2", "Acme", "Paris")
    id1, _ = store.upsert_application(h1, title="Role 1", company="Acme")
    id2, _ = store.upsert_application(h2, title="Role 2", company="Acme")
    store.update_application(id1, status="applied")
    applied = store.list_applications(status="applied")
    assert {r["id"] for r in applied} == {id1}
    seen = store.list_applications(status="seen")
    assert {r["id"] for r in seen} == {id2}


def test_list_applications_filters_by_company_like(store):
    store.upsert_application(
        store.compute_job_hash("R1", "BNP Paribas", "Paris"),
        title="R1", company="BNP Paribas",
    )
    store.upsert_application(
        store.compute_job_hash("R2", "Société Générale", "Paris"),
        title="R2", company="Société Générale",
    )
    store.upsert_application(
        store.compute_job_hash("R3", "Crédit Agricole", "Paris"),
        title="R3", company="Crédit Agricole",
    )
    rows = store.list_applications(company="BNP")
    assert len(rows) == 1
    assert rows[0]["company"] == "BNP Paribas"
    rows = store.list_applications(company="Générale")
    assert len(rows) == 1


def test_list_applications_filters_by_date_range(store):
    """Les bornes since/until comparent les created_at ISO 8601."""
    h = store.compute_job_hash("R", "Acme", "Paris")
    store.upsert_application(h, title="R", company="Acme")
    row = store.list_applications()[0]
    created = row["created_at"]

    # Inclusif sur since
    assert len(store.list_applications(since=created)) == 1
    # Avant created → vide
    assert len(store.list_applications(until="2000-01-01")) == 0
    # Après created → vide
    assert len(store.list_applications(since="2999-01-01")) == 0


# ──────────────────────────────────────────────────────────────────────────
# Cache scrapes
# ──────────────────────────────────────────────────────────────────────────


def test_scrape_cache_miss_returns_none(store):
    assert store.get_cached_scrape("nonexistent" * 8) is None


def test_scrape_cache_save_then_get(store):
    h = store.compute_job_hash("ML Eng", "Acme", "Paris")
    essentials = {
        "title": "ML Eng", "company": "Acme",
        "skills": ["Python", "Spark"], "llm_used": True,
    }
    store.save_scrape_cache(h, essentials)
    cached = store.get_cached_scrape(h)
    assert cached == essentials


def test_scrape_cache_upserts_existing(store):
    h = store.compute_job_hash("ML Eng", "Acme", "Paris")
    store.save_scrape_cache(h, {"title": "Old"})
    store.save_scrape_cache(h, {"title": "New"})
    assert store.get_cached_scrape(h) == {"title": "New"}


def test_scrape_cache_handles_unicode_payload(store):
    """Le JSON doit préserver les accents (ensure_ascii=False côté store)."""
    h = store.compute_job_hash("ML Eng", "L'Oréal", "Paris")
    payload = {"company": "L'Oréal", "summary": "Mission stratégique"}
    store.save_scrape_cache(h, payload)
    cached = store.get_cached_scrape(h)
    assert cached["company"] == "L'Oréal"
    assert cached["summary"] == "Mission stratégique"

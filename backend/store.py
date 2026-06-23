"""SQLite store pour le suivi des candidatures + cache de /scrape-job.

Conventions roadmap respectées : sqlite3 stdlib uniquement, pas d'ORM ni de
migrations Alembic. CREATE TABLE IF NOT EXISTS suffit, le schéma est figé
et le module est idempotent.

Schéma :
    applications  -- une ligne par offre vue, déduplication via job_hash.
                     Le status est conservé entre les re-scrapes (un user qui
                     revoit une offre déjà 'applied' ne perd pas ce statut).
    scrapes       -- cache key->value des essentials retournés par /scrape-job
                     pour économiser un appel Gemini sur une offre déjà vue.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TypedDict

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Chemin DB + connexion
# ──────────────────────────────────────────────────────────────────────────


def _db_path() -> Path:
    """Résout DB_PATH depuis l'env, fallback backend/data/applications.db.

    Évalué à chaque appel pour laisser les tests monkeypatcher l'env (sinon
    on capturerait la valeur au moment de l'import et plus aucune isolation
    serait possible).
    """
    raw = os.getenv("DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).parent / "data" / "applications.db"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Connexion SQLite à usage unique (open-close par appel).

    sqlite3 supporte parfaitement ce pattern — la DB n'est pas tenue ouverte
    entre les requêtes, ce qui évite tout problème de partage entre threads
    (uvicorn lance des handlers async en parallèle).
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────
# Schéma + init
# ──────────────────────────────────────────────────────────────────────────


VALID_STATUSES: tuple[str, ...] = (
    "seen",
    "applied",
    "followed_up",
    "interview",
    "response_pos",
    "response_neg",
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_url TEXT,
    job_hash TEXT NOT NULL UNIQUE,
    company TEXT,
    title TEXT,
    location TEXT,
    contract_type TEXT,
    status TEXT NOT NULL DEFAULT 'seen',
    match_score REAL,
    cv_path TEXT,
    cover_letter_path TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company);
CREATE INDEX IF NOT EXISTS idx_applications_created_at ON applications(created_at);

CREATE TABLE IF NOT EXISTS scrapes (
    job_hash TEXT PRIMARY KEY,
    essentials_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    """Crée la DB et les tables si elles n'existent pas. Idempotent."""
    with _conn() as c:
        c.executescript(_SCHEMA)
        c.commit()
    logger.info("store: DB prête -> %s", _db_path())


# ──────────────────────────────────────────────────────────────────────────
# Hashing canonical
# ──────────────────────────────────────────────────────────────────────────


def compute_job_hash(
    title: str | None,
    company: str | None,
    location: str | None,
) -> str:
    """sha256 d'un canonical normalisé title|company|location.

    Normalisation : NFKD + drop accents, espaces collapsés, lowercase. Garantit
    que "L'Oréal" et "L'Oreal", "Paris " et "Paris" hashent au même endroit
    et donc qu'on ne stocke pas deux applications pour la même offre.
    """
    parts: list[str] = []
    for raw in (title, company, location):
        if raw is None:
            parts.append("")
            continue
        s = unicodedata.normalize("NFKD", str(raw))
        s = s.encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"\s+", " ", s).strip().lower()
        parts.append(s)
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────
# Application rows
# ──────────────────────────────────────────────────────────────────────────


class ApplicationRow(TypedDict, total=False):
    id: int
    job_url: str | None
    job_hash: str
    company: str | None
    title: str | None
    location: str | None
    contract_type: str | None
    status: str
    match_score: float | None
    cv_path: str | None
    cover_letter_path: str | None
    notes: str | None
    created_at: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_application(
    job_hash: str,
    *,
    job_url: str | None = None,
    company: str | None = None,
    title: str | None = None,
    location: str | None = None,
    contract_type: str | None = None,
    match_score: float | None = None,
) -> tuple[int, bool]:
    """Insère ou met à jour un enregistrement application.

    Première vue : INSERT avec status='seen', created_at=now.
    Vue suivante : UPDATE des champs scraping (title, company, etc.) via
    COALESCE pour ne pas écraser une valeur connue avec NULL ; le status
    et created_at NE SONT PAS touchés (préserve les transitions manuelles
    et le timestamp d'ajout).

    Returns:
        (application_id, was_new) — was_new=True si on vient de créer la
        ligne (utile pour le badge "première fois" vs "déjà vu").
    """
    now = _utc_now()
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM applications WHERE job_hash = ?",
            (job_hash,),
        ).fetchone()

        if existing is None:
            cur = c.execute(
                """
                INSERT INTO applications (
                    job_url, job_hash, company, title, location, contract_type,
                    status, match_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'seen', ?, ?, ?)
                """,
                (
                    job_url, job_hash, company, title, location, contract_type,
                    match_score, now, now,
                ),
            )
            c.commit()
            return cur.lastrowid, True

        c.execute(
            """
            UPDATE applications SET
                job_url       = COALESCE(?, job_url),
                company       = COALESCE(?, company),
                title         = COALESCE(?, title),
                location      = COALESCE(?, location),
                contract_type = COALESCE(?, contract_type),
                match_score   = COALESCE(?, match_score),
                updated_at    = ?
            WHERE id = ?
            """,
            (
                job_url, company, title, location, contract_type,
                match_score, now, existing["id"],
            ),
        )
        c.commit()
        return existing["id"], False


def get_application(application_id: int) -> ApplicationRow | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
    return dict(row) if row else None


def get_application_by_hash(job_hash: str) -> ApplicationRow | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM applications WHERE job_hash = ?", (job_hash,)
        ).fetchone()
    return dict(row) if row else None


def list_applications(
    *,
    status: str | None = None,
    company: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[ApplicationRow]:
    """Liste les applications avec filtres optionnels.

    `company` matche en sous-chaîne (LIKE %company%). `since` / `until`
    comparent created_at en ISO 8601 (les strings ISO triient
    lexicographiquement comme des dates).
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if company:
        clauses.append("company LIKE ?")
        params.append(f"%{company}%")
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if until:
        clauses.append("created_at <= ?")
        params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    query = f"SELECT * FROM applications{where} ORDER BY created_at DESC"
    with _conn() as c:
        rows = c.execute(query, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def update_application(
    application_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
    cv_path: str | None = None,
    cover_letter_path: str | None = None,
) -> ApplicationRow | None:
    """PATCH partiel — ne touche que les champs explicitement fournis (non-None).

    Returns la ligne mise à jour, ou None si l'id n'existe pas.
    Raises ValueError si status n'est pas dans VALID_STATUSES.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"status invalide: {status!r} (attendu un de {', '.join(VALID_STATUSES)})"
        )
    updates: list[str] = []
    params: list[Any] = []
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)
    if cv_path is not None:
        updates.append("cv_path = ?")
        params.append(cv_path)
    if cover_letter_path is not None:
        updates.append("cover_letter_path = ?")
        params.append(cover_letter_path)

    if not updates:
        # Pas de patch effectif : on retourne quand même la ligne actuelle
        # (ou None si l'id n'existe pas) — comportement GET-like sans mutation.
        return get_application(application_id)

    updates.append("updated_at = ?")
    params.append(_utc_now())
    params.append(application_id)

    with _conn() as c:
        cur = c.execute(
            f"UPDATE applications SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        c.commit()
        if cur.rowcount == 0:
            return None
        row = c.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
    return dict(row)


# ──────────────────────────────────────────────────────────────────────────
# Cache scrapes
# ──────────────────────────────────────────────────────────────────────────


def get_cached_scrape(job_hash: str) -> dict[str, Any] | None:
    """Récupère un essentials JSON cached. None si miss ou JSON corrompu."""
    with _conn() as c:
        row = c.execute(
            "SELECT essentials_json FROM scrapes WHERE job_hash = ?",
            (job_hash,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["essentials_json"])
    except json.JSONDecodeError:
        logger.warning("store: scrape cache corrompu pour %s, ignoré", job_hash[:12])
        return None


def save_scrape_cache(job_hash: str, essentials: dict[str, Any]) -> None:
    """Upsert (job_hash, essentials_json). Réécrit l'entrée existante."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO scrapes (job_hash, essentials_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(job_hash) DO UPDATE SET
                essentials_json = excluded.essentials_json,
                created_at = excluded.created_at
            """,
            (job_hash, json.dumps(essentials, ensure_ascii=False), _utc_now()),
        )
        c.commit()

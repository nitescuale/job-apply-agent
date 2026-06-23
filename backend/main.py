import asyncio
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from backend.agents.job_scraper import scrape_job  # noqa: E402
from backend.agents import llm_extractor  # noqa: E402
from backend.agents import form_filler  # noqa: E402
from backend.agents import cv_tailor  # noqa: E402
from backend.agents import cover_letter  # noqa: E402
from backend.agents import match_scorer  # noqa: E402
from backend import store  # noqa: E402

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"{date.today()}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Init de la DB SQLite au démarrage. Idempotent (CREATE IF NOT EXISTS)."""
    store.init_db()
    yield


app = FastAPI(title="Job Apply Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScrapeRequest(BaseModel):
    job_url: str
    job_html: str


class FillFormRequest(BaseModel):
    form_schema: dict
    context: dict | None = None


class TailorCvRequest(BaseModel):
    offer: dict
    match: dict | None = None  # optionnel : {matched_skills, missing_skills}


class MatchScoreRequest(BaseModel):
    offer: dict


class CoverLetterRequest(BaseModel):
    offer: dict
    match: dict | None = None


class OpenFileRequest(BaseModel):
    path: str


class ApplicationPatch(BaseModel):
    status: str | None = None
    notes: str | None = None


@app.get("/health")
async def health():
    logger.info("GET /health")
    return {
        "status": "ok",
        "llm_available": llm_extractor.is_available(),
        "form_filler_available": form_filler.is_available(),
        "cv_tailor_available": cv_tailor.is_available(),
        "cover_letter_available": cover_letter.is_available(),
        "match_scorer_available": match_scorer.is_available(),
    }


@app.post("/fill-form")
async def fill_form_endpoint(request: FillFormRequest):
    """Mappe les champs d'un formulaire de candidature au profil utilisateur via Gemini.

    Args:
        request: form_schema (champs détectés par le content script) + context optionnel
                 (title, company de l'offre courante pour personnaliser la lettre).

    Returns:
        {"values": {field_id: value}, "cv_base64": str|null}
    """
    field_count = len((request.form_schema or {}).get("fields", []))
    logger.info("POST /fill-form — %d champs", field_count)
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, form_filler.fill_form, request.form_schema, request.context),
            timeout=30.0,
        )
        return result
    except FileNotFoundError as exc:
        logger.error("fill-form: profil absent — %s", exc)
        raise HTTPException(status_code=412, detail=str(exc))
    except RuntimeError as exc:
        logger.error("fill-form: erreur — %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except asyncio.TimeoutError:
        logger.error("fill-form: timeout")
        raise HTTPException(status_code=504, detail="Form filling timeout")
    except Exception as exc:  # noqa: BLE001
        logger.exception("fill-form: erreur inattendue")
        raise HTTPException(status_code=500, detail=f"Fill error: {exc}")


@app.post("/tailor-cv")
async def tailor_cv_endpoint(request: TailorCvRequest):
    """Génère un CV adapté à l'offre via Gemini et le sauve en PDF.

    Args:
        request.offer: champs structurés de l'offre (title, company, ...).

    Returns:
        {"saved_path", "filename", "folder", "markdown"} — le markdown est
        inclus pour permettre une preview côté popup avant ouverture du PDF.
    """
    offer = request.offer or {}
    company = offer.get("company")
    title = offer.get("title")
    location = offer.get("location")
    logger.info("POST /tailor-cv — title=%r company=%r", title, company)
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None, cv_tailor.tailor_cv, request.offer, request.match
            ),
            timeout=60.0,
        )
        # Écrit cv_path dans l'application correspondante (créée par
        # /scrape-job). Si elle n'existe pas encore — l'user a tailoré sans
        # passer par /scrape-job — on l'upsert au passage.
        job_hash = store.compute_job_hash(title, company, location)
        row = store.get_application_by_hash(job_hash)
        if row is None:
            app_id, _ = store.upsert_application(
                job_hash, company=company, title=title, location=location,
            )
        else:
            app_id = row["id"]
        store.update_application(app_id, cv_path=result.get("saved_path"))
        return result
    except FileNotFoundError as exc:
        logger.error("tailor-cv: ressource absente — %s", exc)
        raise HTTPException(status_code=412, detail=str(exc))
    except ValueError as exc:
        logger.error("tailor-cv: validation — %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        logger.error("tailor-cv: erreur — %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except asyncio.TimeoutError:
        logger.error("tailor-cv: timeout")
        raise HTTPException(status_code=504, detail="CV tailoring timeout")
    except Exception as exc:  # noqa: BLE001
        logger.exception("tailor-cv: erreur inattendue")
        raise HTTPException(status_code=500, detail=f"Tailor error: {exc}")


@app.post("/cover-letter")
async def cover_letter_endpoint(request: CoverLetterRequest):
    """Génère une lettre de motivation long-form via Gemini, en sauve le
    DOCX et le PDF côte à côte avec le CV, et persiste `cover_letter_path`
    sur l'application correspondante.

    Mirror du pipeline /tailor-cv : même dossier entreprise, conversion
    PDF via `pdf_convert.convert_docx_to_pdf` (docx2pdf -> LibreOffice).
    """
    offer = request.offer or {}
    title = offer.get("title")
    company = offer.get("company")
    location = offer.get("location")
    logger.info("POST /cover-letter — title=%r company=%r", title, company)

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None, cover_letter.tailor_cover_letter, request.offer, request.match
            ),
            timeout=60.0,
        )
        # Persistance — comme pour le CV, on rattache à l'application liée
        # par job_hash. Si pas d'application existante (l'user a généré une
        # lettre sans scraper avant), on en crée une avec status='seen'.
        job_hash = store.compute_job_hash(title, company, location)
        row = store.get_application_by_hash(job_hash)
        if row is None:
            app_id, _ = store.upsert_application(
                job_hash, company=company, title=title, location=location,
            )
        else:
            app_id = row["id"]
        store.update_application(app_id, cover_letter_path=result.get("saved_path"))
        return result
    except FileNotFoundError as exc:
        logger.error("cover-letter: ressource absente — %s", exc)
        raise HTTPException(status_code=412, detail=str(exc))
    except ValueError as exc:
        logger.error("cover-letter: validation — %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        logger.error("cover-letter: erreur — %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except asyncio.TimeoutError:
        logger.error("cover-letter: timeout")
        raise HTTPException(status_code=504, detail="Cover letter timeout")
    except Exception as exc:  # noqa: BLE001
        logger.exception("cover-letter: erreur inattendue")
        raise HTTPException(status_code=500, detail=f"Cover letter error: {exc}")


@app.post("/match-score")
async def match_score_endpoint(request: MatchScoreRequest):
    """Score 0-100 de pertinence offre / profil utilisateur.

    Cascade : Gemini (si clé dispo) → fallback overlap déterministe.
    Toujours renvoie `{score, matched_skills, missing_skills, rationale,
    llm_used}` — l'agent ne lève jamais d'exception côté logique métier.

    Persiste `match_score` dans `applications` si une ligne existe pour ce
    job_hash (l'offre a été scrappée au moins une fois).
    """
    offer = request.offer or {}
    title = offer.get("title")
    company = offer.get("company")
    location = offer.get("location")
    logger.info("POST /match-score — title=%r company=%r", title, company)

    try:
        profile = form_filler.load_profile()
    except FileNotFoundError as exc:
        logger.error("match-score: profil absent — %s", exc)
        raise HTTPException(status_code=412, detail=str(exc))

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, match_scorer.score_match, offer, profile),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.error("match-score: timeout")
        raise HTTPException(status_code=504, detail="Match score timeout")
    except Exception as exc:  # noqa: BLE001
        logger.exception("match-score: erreur inattendue")
        raise HTTPException(status_code=500, detail=f"Match score error: {exc}")

    # Persistance opportuniste : seulement si l'offre est déjà trackée.
    # On ne crée PAS d'application ici — c'est le rôle de /scrape-job.
    job_hash = store.compute_job_hash(title, company, location)
    row = store.get_application_by_hash(job_hash)
    if row:
        try:
            store.set_match_score(row["id"], result["score"])
            result["application_id"] = row["id"]
        except Exception:  # noqa: BLE001
            logger.exception("match-score: persistance échouée (non bloquant)")

    return result


@app.post("/open-file")
async def open_file_endpoint(request: OpenFileRequest):
    """Ouvre un fichier local (PDF tailoré, DOCX intermédiaire) dans le
    lecteur par défaut de l'OS.

    Existence d'un endpoint backend pour ça : Chrome MV3 et Firefox bloquent
    silencieusement `chrome.tabs.create({url: 'file://...'})` à moins
    d'avoir activé manuellement "Autoriser l'accès aux URL de fichier"
    dans about:addons / chrome://extensions. Plutôt que de demander à
    l'utilisateur ce flag, on passe par l'OS : `os.startfile` sur Windows,
    `open` sur macOS, `xdg-open` sur Linux. L'utilisateur voit son PDF
    dans son lecteur habituel (Acrobat, Edge, Preview, etc.).

    Sécurité : on n'ouvre QUE des fichiers situés sous `cv_output_dir`
    du profil, et UNIQUEMENT avec extensions .pdf ou .docx. Ça empêche
    qu'un appel malformé déclenche l'exécution d'un binaire arbitraire.
    """
    p = Path(request.path).expanduser().resolve()

    profile = form_filler.load_profile()
    allowed_root = (profile.get("cv_output_dir") or "").strip()
    if not allowed_root:
        raise HTTPException(status_code=412, detail="cv_output_dir non configuré")
    allowed = Path(allowed_root).expanduser().resolve()

    if not p.is_relative_to(allowed):
        raise HTTPException(
            status_code=403,
            detail=f"Chemin hors cv_output_dir: {p}",
        )
    if p.suffix.lower() not in (".pdf", ".docx"):
        raise HTTPException(
            status_code=403,
            detail=f"Extension non autorisée: {p.suffix}",
        )
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable: {p}")

    logger.info("POST /open-file -> %s", p)
    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as exc:  # noqa: BLE001
        logger.exception("open-file: erreur OS")
        raise HTTPException(status_code=500, detail=f"OS open error: {exc}")

    return {"opened": str(p)}


@app.post("/scrape-job")
async def scrape_job_endpoint(request: ScrapeRequest):
    """Extrait les infos d'une offre, court-circuite via cache si déjà vue.

    Pipeline :
        1. scrape_job — extraction structurelle (JSON-LD, meta, fallback texte)
        2. compute_job_hash sur title|company|location normalisés
        3. cache hit -> renvoie les essentials stockés avec `from_cache: true`,
           pas d'appel LLM
        4. cache miss -> llm_extractor (si dispo), écrit dans le cache,
           upsert l'application avec status='seen'
        5. dans tous les cas, renvoie {application_id, seen_before,
           application_status} pour le badge de la popup

    Returns:
        dict avec les essentials de l'offre + flags de tracking.
    """
    logger.info("POST /scrape-job — url=%s html_size=%d", request.job_url, len(request.job_html))
    loop = asyncio.get_running_loop()

    # 1. Scraping structurel
    try:
        scraped = await asyncio.wait_for(
            loop.run_in_executor(None, scrape_job, request.job_html, request.job_url),
            timeout=15.0,
        )
    except ValueError as exc:
        logger.error("scrape-job: validation error — %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except asyncio.TimeoutError:
        logger.error("scrape-job: scraping timeout")
        raise HTTPException(status_code=504, detail="Scraping timeout")
    except Exception as exc:  # noqa: BLE001
        logger.exception("scrape-job: scraping error")
        raise HTTPException(status_code=500, detail=f"Scraping error: {exc}")

    # 2. Hash canonical à partir des champs structurels (pré-LLM). On utilise
    #    toujours le même hash en lecture et en écriture pour que la dédup
    #    et le cache convergent sur une nouvelle visite.
    job_hash = store.compute_job_hash(
        scraped.get("title"), scraped.get("company"), scraped.get("location"),
    )

    # 3. Cache hit -> on saute Gemini
    cached = store.get_cached_scrape(job_hash)
    if cached is not None:
        logger.info("scrape-job: cache hit pour %s", job_hash[:12])
        app_id, was_new = store.upsert_application(
            job_hash,
            job_url=request.job_url,
            company=cached.get("company"),
            title=cached.get("title"),
            location=cached.get("location"),
            contract_type=cached.get("contract_type"),
        )
        row = store.get_application(app_id) or {}
        result = dict(cached)
        result["url"] = request.job_url
        result["from_cache"] = True
        result["application_id"] = app_id
        result["seen_before"] = not was_new
        result["application_status"] = row.get("status", "seen")
        return result

    # 4. Cache miss -> on construit le résultat, on appelle Gemini si dispo
    result: dict = dict(scraped)
    result["llm_used"] = False

    if llm_extractor.is_available():
        try:
            essentials = await asyncio.wait_for(
                loop.run_in_executor(None, llm_extractor.extract_essentials, scraped),
                timeout=30.0,
            )
            for key, value in essentials.items():
                if value not in (None, "", []):
                    result[key] = value
            result["llm_used"] = True
            logger.info("scrape-job: étape LLM OK")
        except Exception as exc:  # noqa: BLE001
            logger.error("scrape-job: étape LLM échouée, renvoi du scraping brut — %s", exc)
            result["llm_error"] = f"{type(exc).__name__}: {exc}"
    else:
        logger.info("scrape-job: GEMINI_API_KEY absente, étape LLM ignorée")

    # 5. Réaffirme l'URL (défense contre un éventuel override Gemini)
    result["url"] = request.job_url

    # 6. Cache + upsert tracking
    store.save_scrape_cache(job_hash, result)
    app_id, was_new = store.upsert_application(
        job_hash,
        job_url=request.job_url,
        company=result.get("company"),
        title=result.get("title"),
        location=result.get("location"),
        contract_type=result.get("contract_type"),
    )
    row = store.get_application(app_id) or {}
    result["from_cache"] = False
    result["application_id"] = app_id
    result["seen_before"] = not was_new
    result["application_status"] = row.get("status", "seen")

    return result


# ──────────────────────────────────────────────────────────────────────────
# Applications tracking
# ──────────────────────────────────────────────────────────────────────────


@app.get("/applications")
async def list_applications_endpoint(
    status: str | None = None,
    company: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    """Liste les candidatures avec filtres optionnels.

    - `status` : exact (seen, applied, followed_up, interview, response_pos, response_neg)
    - `company` : sous-chaîne (LIKE %company%)
    - `since` / `until` : bornes ISO 8601 sur created_at
    """
    return store.list_applications(
        status=status, company=company, since=since, until=until,
    )


@app.get("/applications/{application_id}")
async def get_application_endpoint(application_id: int):
    row = store.get_application(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application introuvable")
    return row


@app.patch("/applications/{application_id}")
async def patch_application_endpoint(application_id: int, patch: ApplicationPatch):
    """Met à jour status et/ou notes. Validation du status côté store."""
    try:
        row = store.update_application(
            application_id,
            status=patch.status,
            notes=patch.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not row:
        raise HTTPException(status_code=404, detail="Application introuvable")
    return row

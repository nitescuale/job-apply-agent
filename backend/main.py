import asyncio
import logging
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

app = FastAPI(title="Job Apply Agent API")

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


@app.get("/health")
async def health():
    logger.info("GET /health")
    return {
        "status": "ok",
        "llm_available": llm_extractor.is_available(),
        "form_filler_available": form_filler.is_available(),
        "cv_tailor_available": cv_tailor.is_available(),
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
    company = (request.offer or {}).get("company")
    title = (request.offer or {}).get("title")
    logger.info("POST /tailor-cv — title=%r company=%r", title, company)
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, cv_tailor.tailor_cv, request.offer),
            timeout=60.0,
        )
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


@app.post("/scrape-job")
async def scrape_job_endpoint(request: ScrapeRequest):
    """Extrait les infos d'une offre depuis le HTML, puis les affine via Gemini.

    Pipeline :
        1. scrape_job — extraction structurelle (JSON-LD, meta, fallback texte)
        2. llm_extractor — moulinette Gemini qui filtre le bruit et structure
           l'essentiel (skills, missions, résumé). Étape ignorée si pas de clé API.

    Args:
        request: Body JSON contenant job_url et job_html

    Returns:
        dict fusionnant le scraping brut et les champs nettoyés par le LLM,
        plus un flag `llm_used`.
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

    result: dict = dict(scraped)
    result["llm_used"] = False

    # 2. Moulinette LLM (optionnelle — dégrade proprement si indisponible)
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

    # Réaffirme l'URL après le merge LLM : protège contre une éventuelle
    # surcharge (la passe Gemini ne devrait pas renvoyer 'url', mais on
    # garantit ici que la popup peut toujours ouvrir l'offre d'origine
    # et que le pipeline cv_tailor reçoit bien l'URL.)
    result["url"] = request.job_url

    return result

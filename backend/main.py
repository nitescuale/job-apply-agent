import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from backend.agents.orchestrator import run_pipeline  # noqa: E402

# Logging vers backend/logs/{date}.log
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

DATA_DIR = Path(__file__).parent / "data"

app = FastAPI(title="Job Apply Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    job_url: str
    job_text: str


@app.get("/health")
async def health():
    logger.info("GET /health")
    return {"status": "ok"}


@app.get("/cv")
async def get_cv():
    logger.info("GET /cv")
    cv_path = DATA_DIR / "cv_base.json"
    with open(cv_path, encoding="utf-8") as f:
        return json.load(f)


@app.post("/analyze-and-adapt")
async def analyze_and_adapt(request: AnalyzeRequest):
    """Analyse une offre d'emploi et retourne le CV adapté.

    Args:
        request: Body JSON contenant job_url et job_text

    Returns:
        dict avec job_data, adapted_cv et match_score
    """
    logger.info("POST /analyze-and-adapt — url=%s", request.job_url)
    try:
        result = await run_pipeline(request.job_url, request.job_text)
        return result
    except ValueError as exc:
        logger.error("analyze-and-adapt: validation error — %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("analyze-and-adapt: unexpected error — %s", exc)
        raise HTTPException(status_code=500, detail="Pipeline error")

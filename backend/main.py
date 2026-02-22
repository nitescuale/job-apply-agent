import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

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
    logger.info("POST /analyze-and-adapt — url=%s", request.job_url)
    # TODO: brancher orchestrateur
    return {"job_data": {}, "adapted_cv": {}, "match_score": 0.0}

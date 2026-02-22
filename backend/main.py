from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Job Apply Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Extensions Chrome + dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    job_url: str
    job_text: str

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/cv")
async def get_cv():
    # TODO: lire cv_base.json
    return {}

@app.post("/analyze-and-adapt")
async def analyze_and_adapt(request: AnalyzeRequest):
    # TODO: brancher orchestrateur
    return {"job_data": {}, "adapted_cv": {}, "match_score": 0.0}

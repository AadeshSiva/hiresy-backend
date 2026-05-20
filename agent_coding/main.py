#agent_coding/main.py
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
import json, requests, uuid, re
from datetime import datetime, timezone

from agent_common.config import (
    GROQ_API_KEY, GROQ_URL, GROQ_MODEL,
    ORCHESTRATOR_URL, FRONTEND_URL,
)
from agent_common.database import get_db, engine, init_db
from agent_common.models import Base, CodingSession
from agent_common.round_router import trigger_next_round

app = FastAPI(title="Hiersy Coding Round Agent")

@app.on_event("startup")
def startup_event():
    init_db()

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["http://localhost:5173", "https://hiresyai.vercel.app"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# ── Round index helper ────────────────────────────────────────────────────────
def _get_round_index(application_id: int, job_id: int) -> int:
    try:
        app_res = requests.get(f"{ORCHESTRATOR_URL}/application/{application_id}", timeout=5)
        if app_res.ok:
            status = app_res.json().get("status", "round_1")
            m = re.match(r"round_(\d+)", status or "")
            if m:
                return int(m.group(1)) - 1
    except Exception as e:
        print(f"[Coding] round index lookup failed: {e}")
    return 0

def _reject(application_id: int):
    try:
        requests.patch(f"{ORCHESTRATOR_URL}/applications/{application_id}/status",
                       json={"status": "rejected"}, timeout=5)
    except: pass

# ------------------- Request Models -------------------
class CreateCodingRequest(BaseModel):
    application_id: int
    job_id: int
    candidate_name: str
    candidate_email: str
    job_title: str
    job_skills: str
    duration_mins: int = 60

class SubmitCodeRequest(BaseModel):
    submissions: List[dict]

# ------------------- Problem generator -------------------
def generate_problems(job_title: str, skills: str) -> list:
    prompt = (
        f'You are a senior technical interviewer. Generate exactly 2 coding problems '
        f'for a "{job_title}" role. Skills focus: {skills}.\n\n'
        'Requirements:\n'
        '- Problem 1: Easy-medium difficulty (arrays, strings, basic logic)\n'
        '- Problem 2: Medium difficulty (data structures, algorithms)\n'
        '- Each problem must be solvable in Python, JavaScript, Java, or C++\n'
        '- Include clear problem statement, input/output format, constraints, and 2 examples\n\n'
        'Return ONLY a valid JSON array, no markdown:\n'
        '[{\n'
        '  "title": "Two Sum",\n'
        '  "difficulty": "easy",\n'
        '  "description": "Given an array...",\n'
        '  "input_format": "...",\n'
        '  "output_format": "...",\n'
        '  "constraints": ["2 <= nums.length <= 10^4"],\n'
        '  "examples": [{"input": "...", "output": "...", "explanation": "..."}],\n'
        '  "starter_code": {"python": "...", "javascript": "...", "java": "...", "cpp": "..."}\n'
        '}]'
    )
    res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.5, "max_tokens": 4000},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=60,
    )
    res.raise_for_status()
    raw = res.json()["choices"][0]["message"]["content"]
    clean = raw.strip().replace("```json", "").replace("```", "").strip()
    s = clean.find("["); e = clean.rfind("]")
    if s == -1 or e == -1:
        raise Exception("No JSON array in response")
    problems = json.loads(clean[s:e+1])
    if not problems:
        raise Exception("No problems generated")
    return problems[:2]

# ------------------- Endpoints -------------------
@app.get("/health")
def health():
    return {"status": "ok", "groq_configured": bool(GROQ_API_KEY)}

@app.post("/coding/create")
def create_coding(req: CreateCodingRequest, db: Session = Depends(get_db)):
    existing = db.query(CodingSession).filter(CodingSession.application_id == req.application_id).first()
    if existing:
        return {"id": existing.id, "token": existing.token,
                "already_exists": True, "email_sent": existing.email_sent}

    try:
        problems = generate_problems(req.job_title, req.job_skills)
    except Exception as e:
        print(f"Problem generation failed: {e}")
        raise HTTPException(500, f"Problem generation failed: {e}")

    token = str(uuid.uuid4()).replace("-", "")[:24]
    session = CodingSession(
        token=token, application_id=req.application_id, job_id=req.job_id,
        candidate_name=req.candidate_name, candidate_email=req.candidate_email,
        job_title=req.job_title, job_skills=req.job_skills,
        problems_json=json.dumps(problems), duration_mins=req.duration_mins,
        status="pending", email_sent=True,  # email sent by round_router before this call
    )
    db.add(session); db.commit(); db.refresh(session)

    return {"id": session.id, "token": token, "already_exists": False, "email_sent": True}

@app.get("/coding/{token}")
def get_coding(token: str, db: Session = Depends(get_db)):
    s = db.query(CodingSession).filter(CodingSession.token == token).first()
    if not s: raise HTTPException(404, "Session not found")
    if s.status == "submitted":
        return {"status": "submitted", "candidate_name": s.candidate_name, "job_title": s.job_title}
    return {"token": token, "candidate_name": s.candidate_name, "job_title": s.job_title,
            "duration_mins": s.duration_mins, "status": s.status,
            "problems": json.loads(s.problems_json)}

@app.post("/coding/{token}/start")
def start_coding(token: str, db: Session = Depends(get_db)):
    s = db.query(CodingSession).filter(CodingSession.token == token).first()
    if not s: raise HTTPException(404, "Session not found")
    if s.status == "submitted": raise HTTPException(400, "Already submitted")
    if s.status != "started":
        s.status = "started"; s.started_at = datetime.now(timezone.utc); db.commit()
    return {"started_at": str(s.started_at), "duration_mins": s.duration_mins}

@app.post("/coding/{token}/submit")
def submit_coding(token: str, req: SubmitCodeRequest, db: Session = Depends(get_db)):
    s = db.query(CodingSession).filter(CodingSession.token == token).first()
    if not s: raise HTTPException(404, "Session not found")
    if s.status == "submitted": raise HTTPException(400, "Already submitted")

    s.submissions_json = json.dumps(req.submissions)
    s.status = "submitted"; s.submitted_at = datetime.now(timezone.utc)
    db.commit()

    current_idx = _get_round_index(s.application_id, s.job_id)
    print(f"[Coding] app_id={s.application_id} job_id={s.job_id} current_idx={current_idx}")
    try:
        result = trigger_next_round(application_id=s.application_id, current_round_index=current_idx)
        print(f"[Coding] Router result: {result}")
    except Exception as e:
        print(f"[Coding] Router error: {e}")

    return {"status": "submitted", "message": "Code submitted successfully"}

@app.get("/coding/application/{app_id}")
def get_by_application(app_id: int, db: Session = Depends(get_db)):
    s = db.query(CodingSession).filter(CodingSession.application_id == app_id).first()
    if not s: raise HTTPException(404, "No coding round found")
    return {
        "id": s.id, "token": s.token, "application_id": s.application_id,
        "candidate_name": s.candidate_name, "candidate_email": s.candidate_email,
        "job_title": s.job_title, "duration_mins": s.duration_mins,
        "status": s.status, "email_sent": s.email_sent,
        "created_at": str(s.created_at),
        "submitted_at": str(s.submitted_at) if s.submitted_at else None,
    }
# agent_bgv/main.py

import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agent_common.config import GROQ_API_KEY, FRONTEND_URL
from agent_common.database import SessionLocal, init_db
from agent_common.models import BgvRecord, Application, Job, User
from agent_common.email_utils import (
    send_email,
    send_bgv_invite_email,
    build_offer_letter_email,
    build_rejection_email,
)

app = FastAPI(title="Hiersy BGV Service", version="1.0.0")

@app.on_event("startup")
def startup_event():
    init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://hiresyai.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── DB dependency ─────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ─── Pydantic schemas ──────────────────────────────────────────────────────────
class BgvCreateRequest(BaseModel):
    application_id: int
    job_id: int

class SubmitDocumentsRequest(BaseModel):
    aadhaar_b64: Optional[str] = None
    pan_b64: Optional[str] = None
    passport_b64: Optional[str] = None
    degree_cert_b64: Optional[str] = None
    experience_letters_b64: Optional[list] = []
    criminal_affidavit_b64: Optional[str] = None
    selfie_b64: Optional[str] = None
    reference_contacts: Optional[list] = []

class HrReviewRequest(BaseModel):
    overall_status: str  # "passed" | "failed"
    hr_notes: Optional[str] = ""

# ─── Helper ────────────────────────────────────────────────────────────────────
def _get_hr_name(job: Job, db: Session) -> str:
    if job and job.posted_by:
        hr_user = db.query(User).filter(User.email == job.posted_by).first()
        if hr_user:
            return hr_user.name
    return "Hiersy Team"

# ─── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/bgv/create")
def create_bgv_session(req: BgvCreateRequest, db: Session = Depends(get_db)):
    """Called by AllPosts.jsx when HR clicks Accept → status becomes selected."""
    app_row = db.query(Application).filter(Application.id == req.application_id).first()
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")

    job_row = db.query(Job).filter(Job.id == req.job_id).first()
    job_title = job_row.job_name if job_row else "the role"
    hr_name = _get_hr_name(job_row, db)

    token = str(uuid.uuid4())

    bgv = BgvRecord(
        token=token,
        application_id=req.application_id,
        job_id=req.job_id,
        candidate_name=app_row.full_name,
        candidate_email=app_row.email,
        job_title=job_title,
        overall_status="pending",
    )
    db.add(bgv)
    db.commit()
    db.refresh(bgv)

    # Send BGV invite email — link built inside send_bgv_invite_email as /back/{token}
    send_bgv_invite_email(app_row.full_name, app_row.email, token, hr_name)

    return {"token": token, "bgv_id": bgv.id, "status": "created"}


@app.get("/bgv/{token}")
def get_bgv_session(token: str, db: Session = Depends(get_db)):
    """Candidate-facing: returns session metadata for the upload page."""
    bgv = db.query(BgvRecord).filter(BgvRecord.token == token).first()
    if not bgv:
        raise HTTPException(status_code=404, detail="BGV session not found")
    return {
        "token": bgv.token,
        "candidate_name": bgv.candidate_name,
        "candidate_email": bgv.candidate_email,
        "job_title": bgv.job_title,
        "status": bgv.overall_status,
        "submitted_at": bgv.submitted_at,
    }


@app.post("/bgv/{token}/submit")
def submit_bgv_documents(token: str, req: SubmitDocumentsRequest, db: Session = Depends(get_db)):
    """
    Candidate clicks Submit on Background.jsx.
    No AI checks — just mark passed and send offer letter email.
    """
    bgv = db.query(BgvRecord).filter(BgvRecord.token == token).first()
    if not bgv:
        raise HTTPException(status_code=404, detail="BGV session not found")
    if bgv.overall_status not in ("pending", "in_progress"):
        raise HTTPException(status_code=400, detail="BGV already submitted")

    bgv.overall_status = "passed"
    bgv.submitted_at = datetime.utcnow()

    app_row = db.query(Application).filter(Application.id == bgv.application_id).first()
    if app_row:
        app_row.status = "selected"

    job_row = db.query(Job).filter(Job.id == bgv.job_id).first()
    hr_name = _get_hr_name(job_row, db)
    job_title = bgv.job_title or (job_row.job_name if job_row else "the position")

    db.commit()

    # Send offer letter email
    offer_link = f"{FRONTEND_URL}/offer/{token}"
    esign_link = f"{FRONTEND_URL}/offer/{token}/sign"
    html = build_offer_letter_email(bgv.candidate_name, job_title, offer_link, esign_link, hr_name)
    send_email(bgv.candidate_email, f"Offer Letter — {job_title} | Hiersy", html)

    return {"status": "passed", "offer_sent": True}


@app.get("/bgv/{token}/report")
def get_bgv_report(token: str, db: Session = Depends(get_db)):
    """HR-facing: full report."""
    bgv = db.query(BgvRecord).filter(BgvRecord.token == token).first()
    if not bgv:
        raise HTTPException(status_code=404, detail="BGV session not found")
    return {
        "id": bgv.id,
        "token": bgv.token,
        "candidate_name": bgv.candidate_name,
        "candidate_email": bgv.candidate_email,
        "job_title": bgv.job_title,
        "overall_status": bgv.overall_status,
        "submitted_at": bgv.submitted_at,
        "reviewed_at": bgv.reviewed_at,
        "ai_summary": bgv.ai_summary,
        "hr_notes": bgv.hr_notes,
    }


@app.get("/bgv/application/{application_id}/report")
def get_bgv_by_application(application_id: int, db: Session = Depends(get_db)):
    """HR dashboard: get BGV report by application_id."""
    bgv = (
        db.query(BgvRecord)
        .filter(BgvRecord.application_id == application_id)
        .order_by(BgvRecord.id.desc())
        .first()
    )
    if not bgv:
        raise HTTPException(status_code=404, detail="No BGV record for this application")
    return get_bgv_report(bgv.token, db)


@app.post("/bgv/{token}/review")
def hr_review_bgv(token: str, req: HrReviewRequest, db: Session = Depends(get_db)):
    """HR manually overrides BGV outcome."""
    bgv = db.query(BgvRecord).filter(BgvRecord.token == token).first()
    if not bgv:
        raise HTTPException(status_code=404, detail="BGV session not found")

    bgv.overall_status = req.overall_status
    bgv.hr_notes = req.hr_notes
    bgv.reviewed_at = datetime.utcnow()

    app_row = db.query(Application).filter(Application.id == bgv.application_id).first()
    job_row = db.query(Job).filter(Job.id == bgv.job_id).first()
    hr_name = _get_hr_name(job_row, db)
    job_title = bgv.job_title or (job_row.job_name if job_row else "the position")

    if req.overall_status == "passed":
        if app_row:
            app_row.status = "selected"
        db.commit()
        offer_link = f"{FRONTEND_URL}/offer/{token}"
        esign_link = f"{FRONTEND_URL}/offer/{token}/sign"
        html = build_offer_letter_email(bgv.candidate_name, job_title, offer_link, esign_link, hr_name)
        send_email(bgv.candidate_email, f"Offer Letter — {job_title} | Hiersy", html)
    else:
        if app_row:
            app_row.status = "rejected"
        db.commit()
        html = build_rejection_email(bgv.candidate_name, job_title, hr_name)
        send_email(bgv.candidate_email, f"Application Update — {job_title} | Hiersy", html)

    db.commit()
    return {"status": bgv.overall_status, "reviewed_at": bgv.reviewed_at}
# agent_offer/main.py
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean

from agent_common.database import Base, engine, get_db, init_db
from agent_common.config import FRONTEND_URL
from agent_common.email_utils import send_email, build_offer_letter_email

app = FastAPI(title="Hiersy Offer Service")

@app.on_event("startup")
def startup_event():
    init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://hiresy.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Model ──────────────────────────────────────────────────
class OfferRecord(Base):
    __tablename__ = "offer_records"

    id                = Column(Integer, primary_key=True, index=True)
    token             = Column(String, unique=True, index=True, nullable=False)
    application_id    = Column(Integer, nullable=False)
    job_id            = Column(Integer, nullable=False)
    candidate_name    = Column(String)
    candidate_email   = Column(String)
    job_title         = Column(String)
    department        = Column(String)
    work_style        = Column(String)
    job_type          = Column(String)
    salary            = Column(String)
    start_date        = Column(String)
    reporting_manager = Column(String)
    location          = Column(String)
    additional_notes  = Column(Text)
    hr_name           = Column(String)
    signature_b64     = Column(Text, nullable=True)
    signed_at         = Column(DateTime, nullable=True)
    status            = Column(String, default="pending")   # pending / signed
    created_at        = Column(DateTime, default=lambda: datetime.utcnow())
    email_sent        = Column(Boolean, default=False)


# ── POST /offer/create ─────────────────────────────────────
@app.post("/offer/create")
def create_offer(payload: dict, db: Session = Depends(get_db)):
    token = str(uuid.uuid4())

    offer = OfferRecord(
        token             = token,
        application_id    = payload.get("application_id"),
        job_id            = payload.get("job_id"),
        candidate_name    = payload.get("candidate_name"),
        candidate_email   = payload.get("candidate_email"),
        job_title         = payload.get("job_title"),
        department        = payload.get("department"),
        work_style        = payload.get("work_style"),
        job_type          = payload.get("job_type"),
        salary            = payload.get("salary"),
        start_date        = payload.get("start_date"),
        reporting_manager = payload.get("reporting_manager"),
        location          = payload.get("location"),
        additional_notes  = payload.get("additional_notes"),
        hr_name           = payload.get("hr_name"),
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)

    if payload.get("send_email", True):
        try:
            offer_url = f"{FRONTEND_URL}/offer/{token}"
            html = build_offer_letter_email(
                candidate_name = payload.get("candidate_name", ""),
                job_title      = payload.get("job_title", "the role"),
                offer_link     = offer_url,
                esign_link     = offer_url,   # same page — candidate views & signs in one step
                hr_name        = payload.get("hr_name", "The Hiring Team"),
            )
            send_email(
                payload.get("candidate_email"),
                f"Your Offer Letter — {payload.get('job_title', 'Role')} at Hiersy",
                html,
            )
            offer.email_sent = True
            db.commit()
        except Exception as e:
            print(f"[offer] email error: {e}")

    return {"token": token, "offer_id": offer.id, "status": "created"}


# ── GET /offer/{token} ─────────────────────────────────────
@app.get("/offer/{token}")
def get_offer(token: str, db: Session = Depends(get_db)):
    offer = db.query(OfferRecord).filter(OfferRecord.token == token).first()
    if not offer:
        raise HTTPException(404, "Offer not found")
    return {
        "candidate_name":    offer.candidate_name,
        "candidate_email":   offer.candidate_email,
        "job_title":         offer.job_title,
        "department":        offer.department,
        "work_style":        offer.work_style,
        "job_type":          offer.job_type,
        "salary":            offer.salary,
        "start_date":        offer.start_date,
        "reporting_manager": offer.reporting_manager,
        "location":          offer.location,
        "additional_notes":  offer.additional_notes,
        "hr_name":           offer.hr_name,
        "status":            offer.status,
    }


# ── POST /offer/{token}/sign ───────────────────────────────
@app.post("/offer/{token}/sign")
def sign_offer(token: str, payload: dict, db: Session = Depends(get_db)):
    offer = db.query(OfferRecord).filter(OfferRecord.token == token).first()
    if not offer:
        raise HTTPException(404, "Offer not found")
    if offer.status == "signed":
        raise HTTPException(409, "Already signed")
    sig = payload.get("signature_b64", "")
    if not sig or len(sig) < 50:
        raise HTTPException(422, "Invalid signature data")

    offer.signature_b64 = sig
    offer.signed_at     = datetime.utcnow()
    offer.status        = "signed"
    db.commit()

    try:
        from agent_common.config import SMTP_USER
        from agent_common.email_utils import wrap_email_body
        body = wrap_email_body(f"""
            <p>Hi,</p>
            <p><strong>{offer.candidate_name}</strong> has e-signed their offer letter
               for <strong>{offer.job_title}</strong>.</p>
            <p>Signed at: {offer.signed_at.strftime('%d %b %Y, %H:%M UTC')}</p>
        """)
        send_email(SMTP_USER, f"[Hiersy] Offer Signed — {offer.candidate_name}", body)
    except Exception as e:
        print(f"[offer] HR notify error: {e}")

    return {"status": "signed", "signed_at": offer.signed_at.isoformat()}
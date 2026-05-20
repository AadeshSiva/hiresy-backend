import sys
print("Python:", sys.version, flush=True)
print("Starting imports...", flush=True)

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import List
import sqlite3, os, requests, urllib.parse, json as _json

from agent_common.config import (
    ORCHESTRATOR_URL, TEST_SERVICE_URL, CODING_SERVICE_URL, LIVEHR_SERVICE_URL,
    LI_CLIENT_ID, LI_CLIENT_SECRET, LI_REDIRECT_URI, LI_SCOPE,
    FRONTEND_URL
)
from agent_common.database import get_db, engine, init_db
from agent_common.models import Base, User, Job, Application
from agent_common.auth import hash_password, verify_password
from agent_common.email_utils import send_email, build_shortlist_email, build_rejection_email

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_orchestrator.linkedin_poster import generate_and_post

app = FastAPI(title="Hiresy Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://hiresyai.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    init_db()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_automation.db")

def init_linkedin_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(linkedin_accounts)")
    cols = [r[1] for r in cur.fetchall()]
    if cols and "person_urn" not in cols:
        conn.execute("DROP TABLE linkedin_accounts")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS linkedin_accounts (
            hr_email     TEXT PRIMARY KEY,
            person_urn   TEXT,
            access_token TEXT NOT NULL,
            expires_at   TEXT
        )
    """)
    conn.commit()
    conn.close()

init_linkedin_db()

# ─────────────────────────────────────────────────────────
# Shared JSON helper
# ─────────────────────────────────────────────────────────
def _safe_json(raw, fallback):
    """Parse a JSON string that may be double-encoded. Returns fallback on any error."""
    if raw is None:
        return fallback
    if isinstance(raw, (list, dict)):
        return raw
    try:
        val = _json.loads(raw)
        if isinstance(val, str):
            val = _json.loads(val)
        return val if isinstance(val, type(fallback)) else fallback
    except Exception:
        return fallback

# ─────────────────────────────────────────────────────────
# Helper: trigger evaluation (background task)
# ─────────────────────────────────────────────────────────
EVAL_SERVICE_URL = "http://127.0.0.1:8001/eval/evaluate"

def trigger_evaluation(app_id: int, payload: dict):
    try:
        from agent_common.models import Application, Job
        from agent_common.database import SessionLocal
        db2 = SessionLocal()
        app_entry = db2.query(Application).filter(Application.id == app_id).first()
        if not app_entry:
            return
        job = db2.query(Job).filter(Job.id == app_entry.job_id).first()
        job_description = f"{job.job_name}\n{job.description}\nSkills: {job.skills}" if job else ""

        eval_payload = {
            "application_id": app_id,
            "resume_text": f"""
Name: {app_entry.full_name}
Current Title: {app_entry.current_title}
Company: {app_entry.company_name}
Experience: {app_entry.years_exp} years
Education: {app_entry.degree_type} in {app_entry.field_of_study} from {app_entry.institution}
Technical Skills: {app_entry.technical_skills}
Soft Skills: {app_entry.soft_skills}
""",
            "job_description": job_description,
            "github_url": app_entry.github_url or "",
            "linkedin_url": app_entry.linkedin_url or "",
            "leetcode_url": app_entry.leetcode_url or "",
        }
        res = requests.post(EVAL_SERVICE_URL, json=eval_payload, timeout=90)
        if res.ok:
            result = res.json()
            app_entry.eval_score = result.get("final_score")
            app_entry.eval_recommendation = result.get("hiring_recommendation")
            app_entry.eval_summary = result.get("summary")
            app_entry.eval_data = _json.dumps(result)
            db2.commit()
            print(f"[Eval] App {app_id} scored: {result.get('final_score')}")
        else:
            print(f"[Eval] Service error for app {app_id}: {res.text[:200]}")
        db2.close()
    except Exception as e:
        print(f"[Eval] Failed for app {app_id}: {e}")

# ─────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"message": "Hiersy API running"}

# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────
@app.post("/register")
def register(user: dict, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user["email"]).first():
        raise HTTPException(400, "Email already registered")
    db.add(User(name=user["name"], email=user["email"], password=hash_password(user["password"])))
    db.commit()
    return {"message": "Registered successfully"}

@app.post("/login")
def login(user: dict, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.email == user["email"]).first()
    if not db_user or not verify_password(user["password"], db_user.password):
        raise HTTPException(400, "Invalid credentials")
    return {"message": "Login successful", "email": db_user.email, "name": db_user.name}

# ─────────────────────────────────────────────────────────
# LinkedIn OAuth
# ─────────────────────────────────────────────────────────
@app.get("/linkedin/connect")
def linkedin_connect(email: str):
    params = {
        "response_type": "code",
        "client_id": LI_CLIENT_ID,
        "redirect_uri": LI_REDIRECT_URI,
        "scope": LI_SCOPE,
        "state": email,
    }
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)

@app.get("/linkedin/callback")
def linkedin_callback(code: str = None, state: str = None, error: str = None):
    if error or not code:
        return RedirectResponse(f"{FRONTEND_URL}/hrdashboard/all?linkedin=error")
    hr_email = state
    res = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": LI_REDIRECT_URI,
            "client_id": LI_CLIENT_ID,
            "client_secret": LI_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    token_data = res.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return RedirectResponse(f"{FRONTEND_URL}/hrdashboard/all?linkedin=error")
    info = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    person_urn = ""
    if info.status_code == 200:
        sub = info.json().get("sub", "")
        person_urn = f"urn:li:person:{sub}"
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO linkedin_accounts (hr_email, person_urn, access_token, expires_at)
        VALUES (?, ?, ?, datetime('now', '+60 days'))
    """, (hr_email, person_urn, access_token))
    conn.commit()
    conn.close()
    return RedirectResponse(f"{FRONTEND_URL}/hrdashboard/all?linkedin=connected")

@app.get("/linkedin/status/{email}")
def linkedin_status(email: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT person_urn FROM linkedin_accounts WHERE hr_email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return {"connected": bool(row)}

@app.delete("/linkedin/disconnect/{email}")
def linkedin_disconnect(email: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM linkedin_accounts WHERE hr_email = ?", (email,))
    conn.commit()
    conn.close()
    return {"message": "Disconnected"}

# ─────────────────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────────────────
@app.post("/jobs")
def create_job(job: dict, db: Session = Depends(get_db)):
    if not db.query(User).filter(User.email == job["posted_by"]).first():
        raise HTTPException(404, "HR user not found")
    new_job = Job(**job)
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    return new_job

@app.post("/jobs/{job_id}/post-linkedin")
def post_job_to_linkedin(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    job_details = {
        "posted_by": job.posted_by,
        "title": job.job_name,
        "skills": job.skills or "",
        "location": job.work_style or "",
        "salary": f"Rs.{job.salary_start} - Rs.{job.salary_end}" if job.salary_start else "",
        "description": job.description,
        "job_type": job.job_type,
        "department": job.department,
        "openings": job.openings,
        "deadline": job.deadline or "",
    }
    return generate_and_post(str(job_id), job_details)

@app.get("/jobs")
def get_all_jobs(db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.created_at.desc()).all()

@app.get("/jobs/my/{email}")
def get_my_jobs(email: str, db: Session = Depends(get_db)):
    return db.query(Job).filter(Job.posted_by == email).order_by(Job.created_at.desc()).all()

@app.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    return job

@app.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    db.query(Application).filter(Application.job_id == job_id).delete()
    db.query(Job).filter(Job.id == job_id).delete()
    db.commit()
    return {"message": f"Job {job_id} deleted"}

# ─────────────────────────────────────────────────────────
# Applications
# ─────────────────────────────────────────────────────────
@app.post("/applications")
def submit_application(payload: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    valid_columns = {c.name for c in Application.__table__.columns}
    known_fields = {k: v for k, v in payload.items() if k in valid_columns and k != "stage"}
    custom_fields = {k: v for k, v in payload.items() if k not in valid_columns and k != "stage"}
    app_entry = Application(**known_fields)
    app_entry.status = "pending"
    if hasattr(app_entry, "extra_fields") and custom_fields:
        app_entry.extra_fields = _json.dumps(custom_fields)
    db.add(app_entry)
    db.commit()
    db.refresh(app_entry)
    background_tasks.add_task(trigger_evaluation, app_entry.id, payload)
    return {"message": "Application submitted!", "id": app_entry.id}

@app.get("/applications/job/{job_id}/count")
def get_application_count(job_id: int, db: Session = Depends(get_db)):
    return {"count": db.query(Application).filter(Application.job_id == job_id).count()}

@app.get("/applications/{job_id}")
def get_applications(job_id: int, db: Session = Depends(get_db)):
    return db.query(Application).filter(Application.job_id == job_id).all()

@app.get("/application/{app_id}")
def get_single_application(app_id: int, db: Session = Depends(get_db)):
    app_entry = db.query(Application).filter(Application.id == app_id).first()
    if not app_entry:
        raise HTTPException(404, "Application not found")
    job = db.query(Job).filter(Job.id == app_entry.job_id).first()
    data = {c.name: getattr(app_entry, c.name) for c in app_entry.__table__.columns}
    data["job_name"] = job.job_name if job else "Unknown Position"
    return data

@app.patch("/applications/{app_id}/status")
def update_application_status(app_id: int, payload: dict, db: Session = Depends(get_db)):
    app_entry = db.query(Application).filter(Application.id == app_id).first()
    if not app_entry:
        raise HTTPException(404, "Application not found")
    app_entry.status = payload.get("status")
    db.commit()
    return {"message": "Status updated", "status": app_entry.status}

@app.post("/applications/{app_id}/retry-eval")
def retry_evaluation(app_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    app_entry = db.query(Application).filter(Application.id == app_id).first()
    if not app_entry:
        raise HTTPException(404, "Application not found")
    payload = {
        "github_url": app_entry.github_url or "",
        "linkedin_url": app_entry.linkedin_url or "",
        "leetcode_url": app_entry.leetcode_url or "",
        "technical_skills": app_entry.technical_skills or "",
        "soft_skills": app_entry.soft_skills or "",
    }
    background_tasks.add_task(trigger_evaluation, app_id, payload)
    return {"message": "Evaluation retrying..."}

# ─────────────────────────────────────────────────────────
# Round Performance
# ─────────────────────────────────────────────────────────
ROUND_TYPE_MAP = {
    "MCQ Test":                   ("shortlist", "mcq"),
    "Aptitude / Logical":         ("shortlist", "aptitude"),
    "Vibe Coding":                ("shortlist", "vibe_coding"),
    "Verbal Ability Test":        ("comm",      "verbal"),
    "Spoken English Test":        ("spoken",    None),
    "Basic Programming":          ("coding",    None),
    "Technical HR":               ("livehr",    "technical_hr"),
    "Salary & Policy Discussion": ("livehr",    "salary_policy"),
}

@app.get("/applications/{app_id}/round-performance")
def get_round_performance(app_id: int, db: Session = Depends(get_db)):
    from agent_common.models import (
        Application, Job,
        TestSession, CommSession, SpokenTest, CodingSession, LiveSession,
    )

    app_entry = db.query(Application).filter(Application.id == app_id).first()
    if not app_entry:
        raise HTTPException(404, "Application not found")

    job = db.query(Job).filter(Job.id == app_entry.job_id).first()
    if not job or not job.rounds:
        return []

    round_names = [r.strip() for r in job.rounds.split(",") if r.strip()]
    results = []

    for idx, round_name in enumerate(round_names):
        mapping = ROUND_TYPE_MAP.get(round_name)
        if not mapping:
            results.append({
                "round_name":  round_name,
                "round_index": idx + 1,
                "category":    "unknown",
                "data":        None,
            })
            continue

        category, test_type = mapping
        entry = None
        data  = None

        # ── SHORTLIST ─────────────────────────────────────────────────────────
        if category == "shortlist":
            q = db.query(TestSession).filter(TestSession.application_id == app_id)
            # try exact test_type match first, then fall back to latest for this app
            entry = (
                q.filter(TestSession.test_type == test_type).order_by(TestSession.id.desc()).first()
                if test_type else None
            )
            if not entry:
                entry = q.order_by(TestSession.id.desc()).first()

            if entry:
                answers = _safe_json(entry.answers_json, [])

                correct = None
                wrong   = None
                if answers:
                    correct = sum(1 for a in answers if isinstance(a, dict) and a.get("is_correct"))
                    wrong   = len(answers) - correct

                duration_secs = None
                if entry.started_at and entry.submitted_at:
                    duration_secs = int((entry.submitted_at - entry.started_at).total_seconds())

                data = {
                    "score_pct":       entry.score_pct,
                    "passed":          entry.passed,
                    "status":          entry.status,
                    "test_type":       entry.test_type,
                    "total_questions": entry.total_questions,
                    "correct":         correct,
                    "wrong":           wrong,
                    "pass_score":      entry.pass_score,
                    "duration_secs":   duration_secs,
                    "started_at":      entry.started_at.isoformat()   if entry.started_at   else None,
                    "submitted_at":    entry.submitted_at.isoformat() if entry.submitted_at else None,
                    "answers":         answers,
                    "submission_data": entry.submission_data or None,
                }

        # ── VERBAL ────────────────────────────────────────────────────────────
        elif category == "comm":
            q = db.query(CommSession).filter(CommSession.application_id == app_id)
            entry = (
                q.filter(CommSession.test_type == test_type).order_by(CommSession.id.desc()).first()
                if test_type else None
            )
            if not entry:
                entry = q.order_by(CommSession.id.desc()).first()

            if entry:
                answers   = _safe_json(entry.answers_json,   [])
                questions = _safe_json(entry.questions_json, [])

                per_q = []
                for i, ans in enumerate(answers):
                    q_text = (
                        questions[i].get("question", f"Q{i+1}")
                        if i < len(questions) and isinstance(questions[i], dict)
                        else f"Q{i+1}"
                    )
                    per_q.append({
                        "question": q_text,
                        "score":    ans.get("score", 0)      if isinstance(ans, dict) else 0,
                        "max":      ans.get("max_score", 10) if isinstance(ans, dict) else 10,
                    })

                data = {
                    "score_pct":       entry.score_pct,
                    "passed":          entry.passed,
                    "status":          entry.status,
                    "test_type":       entry.test_type,
                    "total_questions": entry.total_questions,
                    "pass_score":      entry.pass_score,
                    "per_question":    per_q,
                    "submitted_at":    entry.submitted_at.isoformat() if entry.submitted_at else None,
                }

        # ── SPOKEN ────────────────────────────────────────────────────────────
        elif category == "spoken":
            entry = (
                db.query(SpokenTest)
                .filter(SpokenTest.application_id == app_id)
                .order_by(SpokenTest.id.desc())
                .first()
            )
            if entry:
                data = {
                    "score":        entry.score,
                    "passed":       entry.passed,
                    "status":       entry.status,
                    "evaluation":   entry.evaluation,
                    "transcript":   entry.transcript,
                    "submitted_at": entry.submitted_at.isoformat() if entry.submitted_at else None,
                }

        # ── CODING ────────────────────────────────────────────────────────────
        elif category == "coding":
            entry = (
                db.query(CodingSession)
                .filter(CodingSession.application_id == app_id)
                .order_by(CodingSession.id.desc())
                .first()
            )
            if entry:
                submissions = _safe_json(entry.submissions_json, [])
                language = (
                    submissions[-1].get("language", "")
                    if submissions and isinstance(submissions[-1], dict)
                    else ""
                )
                data = {
                    "status":       entry.status,
                    "language":     language,
                    "submissions":  submissions,
                    "started_at":   entry.started_at.isoformat()   if entry.started_at   else None,
                    "submitted_at": entry.submitted_at.isoformat() if entry.submitted_at else None,
                }

        # ── LIVE HR ───────────────────────────────────────────────────────────
        elif category == "livehr":
            q = db.query(LiveSession).filter(LiveSession.application_id == app_id)
            # If LiveSession has a session_type column, use it; otherwise just take latest
            if test_type and hasattr(LiveSession, "session_type"):
                entry = q.filter(LiveSession.session_type == test_type).order_by(LiveSession.id.desc()).first()
            if not entry:
                entry = q.order_by(LiveSession.id.desc()).first()

            if entry:
                suggestions = _safe_json(entry.suggestions, [])
                data = {
                    "candidate_score": entry.candidate_score,
                    "outcome":         entry.outcome,
                    "status":          entry.status,
                    "suggestions":     suggestions,
                    "transcript":      entry.transcript,
                }

        results.append({
            "round_name":  round_name,
            "round_index": idx + 1,
            "category":    category,
            "data":        data if entry else None,
        })

    return results

# ─────────────────────────────────────────────────────────
# Rejection email
# ─────────────────────────────────────────────────────────
@app.post("/applications/{app_id}/reject-email")
def send_rejection_email(app_id: int, db: Session = Depends(get_db)):
    app_entry = db.query(Application).filter(Application.id == app_id).first()
    if not app_entry:
        raise HTTPException(404, "Application not found")

    job = db.query(Job).filter(Job.id == app_entry.job_id).first()
    job_title = job.job_name if job else "the position"

    hr_name = "Hiersy Team"
    if job and job.posted_by:
        hr_user = db.query(User).filter(User.email == job.posted_by).first()
        if hr_user:
            hr_name = hr_user.name

    html = build_rejection_email(app_entry.full_name, job_title, hr_name)
    send_email(app_entry.email, f"Application Update — {job_title} | Hiersy", html)
    return {"message": "Rejection email sent"}

# ─────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────
@app.get("/users/by-email/{email}")
def get_user_by_email(email: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(404, "User not found")
    return {"id": user.id, "name": user.name, "email": user.email}

# ─────────────────────────────────────────────────────────
# Resume extraction
# ─────────────────────────────────────────────────────────
@app.post("/extract-resume")
async def extract_resume(request: Request):
    import base64, re, tempfile
    import pdfplumber
    body = await request.json()
    pdf_base64 = body.get("pdf_base64")
    if not pdf_base64:
        raise HTTPException(400, "pdf_base64 required")
    pdf_bytes = base64.b64decode(pdf_base64)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
        f.write(pdf_bytes)
        tmp_path = f.name
    try:
        text = ""
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
    finally:
        os.unlink(tmp_path)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full_text = text

    def find_section(headers):
        for i, line in enumerate(lines):
            for h in headers:
                if re.match(rf"^{re.escape(h)}\s*[:\-]?\s*$", line, re.IGNORECASE):
                    return i
                if re.match(rf"^{re.escape(h)}\b", line, re.IGNORECASE) and len(line) < 40:
                    return i
        return -1

    def get_section_lines(headers, max_lines=20):
        start = find_section(headers)
        if start < 0:
            return []
        section_headers = ["experience","education","skills","projects","certif","awards","publications","languages","interests","summary","objective","profile","work","employment","academic","achievements","extracurricular","volunteer","hobbies","technical","professional","contact","links","portfolio"]
        result = []
        for line in lines[start+1:start+1+max_lines]:
            if any(re.match(rf"^{h}\b", line, re.IGNORECASE) and len(line) < 50 for h in section_headers):
                break
            result.append(line)
        return result

    full_name = ""
    for line in lines[:5]:
        if not re.search(r'[@/\\.]com|^\+?\d[\d\s\-]{8,}|http', line, re.IGNORECASE):
            if len(line.split()) >= 2 and len(line) < 60:
                full_name = line
                break
    if not full_name and lines:
        full_name = lines[0]

    email = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', full_text)
    email = email.group(0) if email else ""

    phone = re.search(r'(\+?[\d][\d\s\-().]{9,14}\d)', full_text)
    phone = phone.group(1).strip() if phone else ""

    def extract_url(pattern):
        m = re.search(pattern, full_text, re.IGNORECASE)
        if not m:
            return ""
        url = m.group(0).strip().rstrip(".,)|>")
        if not url.startswith("http"):
            url = "https://" + url
        return url

    linkedin  = extract_url(r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+')
    github    = extract_url(r'(?:https?://)?(?:www\.)?github\.com/[\w\-]+')
    leetcode  = extract_url(r'(?:https?://)?(?:www\.)?leetcode\.com/[\w\-]+')
    portfolio = extract_url(r'(?:https?://)?[\w\-]+\.(?:vercel\.app|netlify\.app|github\.io)[^\s,]*')

    location = ""
    loc_patterns = [
        r'\b([A-Z][a-zA-Z\s]+,\s*(?:Tamil Nadu|Karnataka|Maharashtra|Delhi|Telangana|Kerala|Gujarat|Rajasthan|Punjab|UP|India|USA|UK|Canada|Australia|Germany|France|Singapore|Remote))\b',
        r'\b([A-Z][a-z]+(?: [A-Z][a-z]+)?,\s*[A-Z][a-z]+(?: [A-Z][a-z]+)?)\b',
    ]
    header_text = "\n".join(lines[:8])
    for pat in loc_patterns:
        m = re.search(pat, header_text)
        if m:
            location = m.group(1).strip()
            break
    if not location:
        city_keywords = ["Chennai","Bangalore","Mumbai","Delhi","Hyderabad","Pune","Kolkata","Ahmedabad","Jaipur","Surat","Lucknow","Kochi","Coimbatore","Madurai","Pondicherry","Puducherry","New York","San Francisco","London","Toronto","Singapore","Remote","Bengaluru","Noida","Gurgaon","Gurugram"]
        for line in lines[:8]:
            for city in city_keywords:
                if city.lower() in line.lower():
                    location = line.strip()
                    break
            if location:
                break
    if not location:
        for pat in loc_patterns:
            m = re.search(pat, full_text)
            if m:
                location = m.group(1).strip()
                break

    technical_skills = ""
    soft_skills_val  = ""
    skill_section = get_section_lines(["skills","technical skills","technologies","tech stack","core competencies","competencies","tools & technologies","tools and technologies","programming skills"], 30)
    if skill_section:
        raw_skills = []
        for line in skill_section:
            colon_match = re.match(r'^[\w\s/&]+:\s*(.+)', line)
            if colon_match:
                raw_skills.append(colon_match.group(1))
            else:
                cleaned = re.sub(r'^[\•\-\*\u2022\u25cf\u25aa►▸→·]\s*', '', line)
                if cleaned:
                    raw_skills.append(cleaned)
        all_skills_str = ", ".join(raw_skills)
        skill_tokens = re.split(r'[,|;•\n]+', all_skills_str)
        skill_tokens = [s.strip() for s in skill_tokens if s.strip() and len(s.strip()) > 1]
        soft_kw = ["communication","teamwork","leadership","problem solving","time management","adaptability","critical thinking","collaboration","interpersonal","presentation","creativity"]
        tech_list, soft_list = [], []
        for skill in skill_tokens:
            if any(kw in skill.lower() for kw in soft_kw):
                soft_list.append(skill)
            else:
                tech_list.append(skill)
        technical_skills = ", ".join(tech_list)
        soft_skills_val  = ", ".join(soft_list)

    if not technical_skills:
        known_tech = ["Python","Java","JavaScript","TypeScript","C++","C#","Ruby","Go","Rust","Kotlin","Swift","PHP","HTML","CSS","React","Angular","Vue","Next.js","Node.js","Django","Flask","FastAPI","MySQL","PostgreSQL","MongoDB","Docker","Kubernetes","AWS","Git","Linux"]
        found = [t for t in known_tech if re.search(rf'\b{t}\b', full_text, re.IGNORECASE)]
        technical_skills = ", ".join(found)

    degree_type    = ""
    field_of_study = ""
    institution    = ""
    edu_lines = get_section_lines(["education","academic background","academic qualifications","qualifications"], 15)
    degree_patterns = [r'\b(B\.?Tech|B\.?E\.?|B\.?Sc\.?|B\.?C\.?A|M\.?Tech|M\.?Sc\.?|M\.?B\.?A|M\.?C\.?A|Ph\.?D|B\.?Com|Diploma|Bachelor|Master|Associate)\b']
    if edu_lines:
        for line in edu_lines:
            for pat in degree_patterns:
                m = re.search(pat, line, re.IGNORECASE)
                if m:
                    degree_type = m.group(1)
                    in_m = re.search(r'(?:in|of)\s+([A-Za-z\s&]+?)(?:\s*[-,|]\s*|\s*\d{4}|$)', line, re.IGNORECASE)
                    if in_m:
                        field_of_study = in_m.group(1).strip()
                    break
            if degree_type:
                for edu_line in edu_lines:
                    if any(kw in edu_line.lower() for kw in ["university","college","institute","institution","school","iit","nit","bits"]):
                        institution = edu_line.strip()
                        break
                break

    years_exp    = ""
    current_title = ""
    company_name  = ""
    exp_patterns = [
        r'(\d+\.?\d*)\s*\+?\s*(?:years?|yrs?)[\s\w]{0,20}(?:of\s+)?(?:experience|exp)',
        r'(?:experience|exp)[^\d]{0,10}(\d+\.?\d*)\s*\+?\s*(?:years?|yrs?)',
    ]
    for pat in exp_patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            years_exp = m.group(1)
            break
    exp_lines = get_section_lines(["experience","work experience","professional experience","employment history","internship","internships","work history"], 20)
    if exp_lines:
        title_patterns = [
            r'^([A-Z][a-zA-Z\s]+(?:Engineer|Developer|Designer|Manager|Analyst|Intern|Lead|Architect|Consultant|Scientist|Specialist))',
            r'^([\w\s]+(?:Intern|Developer|Engineer|Designer|Analyst|Manager|Lead))',
        ]
        for line in exp_lines:
            for pat in title_patterns:
                m = re.match(pat, line)
                if m:
                    current_title = m.group(1).strip()
                    at_m = re.search(r'(?:at|@|,|\|)\s*([A-Z][a-zA-Z\s]+?)(?:\s*[-|,]|\s*\d{4}|$)', line)
                    if at_m:
                        company_name = at_m.group(1).strip()
                    break
            if current_title:
                if not company_name:
                    for next_line in exp_lines[exp_lines.index(line)+1:exp_lines.index(line)+3]:
                        if re.match(r'^[A-Z][a-zA-Z\s]+$', next_line) and len(next_line) < 50:
                            company_name = next_line.strip()
                            break
                break

    return {
        "full_name":        full_name,
        "email":            email,
        "phone":            phone,
        "location":         location,
        "linkedin_url":     linkedin,
        "github_url":       github,
        "leetcode_url":     leetcode,
        "portfolio_url":    portfolio,
        "degree_type":      degree_type,
        "field_of_study":   field_of_study,
        "institution":      institution,
        "years_exp":        years_exp,
        "current_title":    current_title,
        "company_name":     company_name,
        "current_lpa":      "",
        "notice_period":    "",
        "technical_skills": technical_skills,
        "soft_skills":      soft_skills_val,
    }

# ─────────────────────────────────────────────────────────
# LinkedIn profile scrape
# ─────────────────────────────────────────────────────────
@app.get("/linkedin/profile")
def get_linkedin_profile(url: str):
    from agent_orchestrator.linkedin_scraper import scrape_linkedin  # type: ignore
    try:
        data = scrape_linkedin(url)
        return {"success": bool(data.get("name")), "data": data}
    except Exception as e:
        return {"success": False, "data": {}, "error": str(e)}
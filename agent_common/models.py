from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float
from sqlalchemy.sql import func
from .database import Base
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey

# User (from core main.py / models.py)
class User(Base):
    __tablename__ = "users"

    id       = Column(Integer, primary_key=True, index=True)
    name     = Column(String, nullable=False)
    email    = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)


# Job (from core models.py)
class Job(Base):
    __tablename__ = "jobs"

    id                 = Column(Integer, primary_key=True, index=True)
    posted_by          = Column(String, index=True)
    job_name           = Column(String, nullable=False)
    description        = Column(Text, nullable=False)
    salary_start       = Column(String)
    salary_end         = Column(String)
    show_salary        = Column(String, default="false")
    work_style         = Column(String)
    job_type           = Column(String)
    skills             = Column(String)
    exp_min            = Column(String)
    exp_max            = Column(String)
    department         = Column(String)
    openings           = Column(Integer)
    deadline           = Column(String)
    application_fields = Column(Text)
    difficulty         = Column(String)
    rounds             = Column(Text)          # comma-separated round names
    platforms          = Column(String)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())


# Application (from core models.py – fixed duplicates)
class Application(Base):
    __tablename__ = "applications"

    id              = Column(Integer, primary_key=True, index=True)
    job_id          = Column(Integer, nullable=False)
    submitted_at    = Column(DateTime, default=datetime.utcnow)

    # Personal
    full_name       = Column(String)
    email           = Column(String)
    phone           = Column(String)
    alt_phone       = Column(String)
    location        = Column(String)

    # Resume / links
    resume_url      = Column(String)
    linkedin_url    = Column(String)
    github_url      = Column(String)
    leetcode_url    = Column(String)
    portfolio_url   = Column(String)

    # Education
    degree_type     = Column(String)
    field_of_study  = Column(String)
    institution     = Column(String)

    # Experience
    years_exp       = Column(String)
    current_title   = Column(String)
    company_name    = Column(String)
    current_lpa     = Column(String)
    notice_period   = Column(String)

    # Skills
    technical_skills = Column(String)
    soft_skills      = Column(String)

    # Extra
    cover_letter    = Column(String)

    # Status (single definition)
    status          = Column(String, default="pending")   # pending/round_1/round_2/round_3/selected/rejected

    # AI evaluation (single definition)
    eval_score          = Column(String)
    eval_recommendation = Column(String)
    eval_summary        = Column(String)
    eval_data           = Column(Text)          # JSON string


# Shortlisting Test (from shortlistingtest/main.py)
class TestSession(Base):
    __tablename__ = "shortlist_tests"

    id              = Column(Integer, primary_key=True, index=True)
    token           = Column(String, unique=True, index=True)
    application_id  = Column(Integer)
    job_id          = Column(Integer)
    candidate_name  = Column(String)
    candidate_email = Column(String)
    job_title       = Column(String)
    job_skills      = Column(String)
    questions_json  = Column(Text)          # For MCQ/aptitude: list of questions; for vibe: {"problem": "...", "starter_code": "..."}
    duration_mins   = Column(Integer, default=20)
    total_questions = Column(Integer, default=10)  # For vibe coding, this will be 1
    answers_json    = Column(Text, nullable=True)
    score           = Column(Float, nullable=True)
    score_pct       = Column(Float, nullable=True)
    passed          = Column(Boolean, nullable=True)
    pass_score      = Column(Integer, default=60)
    started_at      = Column(DateTime, nullable=True)
    submitted_at    = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=lambda: datetime.utcnow())
    email_sent      = Column(Boolean, default=False)
    status          = Column(String, default="pending")   # pending/started/submitted/pending_review/reviewed
    test_type       = Column(String, default="mcq")       # "mcq", "aptitude", "vibe_coding"
    submission_data = Column(Text, nullable=True)         # For vibe coding: stores submitted code

class CommSession(Base):
    __tablename__ = "comm_tests"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    application_id = Column(Integer)
    job_id = Column(Integer)
    candidate_name = Column(String)
    candidate_email = Column(String)
    job_title = Column(String)
    test_type = Column(String)          # "verbal"
    questions_json = Column(Text)       # JSON array of questions
    duration_mins = Column(Integer, default=10)
    total_questions = Column(Integer)
    answers_json = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    score_pct = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=True)
    pass_score = Column(Integer, default=60)
    started_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.utcnow())
    email_sent = Column(Boolean, default=False)
    status = Column(String, default="pending")   # pending/started/submitted

class SpokenTest(Base):
    __tablename__ = "spoken_tests"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    application_id = Column(Integer)
    job_id = Column(Integer)
    candidate_name = Column(String)
    candidate_email = Column(String)
    job_title = Column(String)
    paragraph = Column(Text)
    topic = Column(Text)
    transcript = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=True)
    evaluation = Column(Text, nullable=True)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.utcnow())
    submitted_at = Column(DateTime, nullable=True)

# Coding Round (from codinground/main.py)
class CodingSession(Base):
    __tablename__ = "coding_rounds"

    id              = Column(Integer, primary_key=True, index=True)
    token           = Column(String, unique=True, index=True)
    application_id  = Column(Integer)
    job_id          = Column(Integer)
    candidate_name  = Column(String)
    candidate_email = Column(String)
    job_title       = Column(String)
    job_skills      = Column(String)
    problems_json   = Column(Text)            # 2 coding problems
    duration_mins   = Column(Integer, default=60)
    submissions_json = Column(Text, nullable=True)   # list of {problem_idx, language, code}
    started_at      = Column(DateTime, nullable=True)
    submitted_at    = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=lambda: datetime.utcnow())
    email_sent      = Column(Boolean, default=False)
    status          = Column(String, default="pending")   # pending/started/submitted


class LiveSession(Base):
    __tablename__ = "live_hr_sessions"

    id              = Column(Integer, primary_key=True, index=True)
    token           = Column(String, unique=True, index=True)
    meet_code       = Column(String)
    application_id  = Column(Integer)
    candidate_name  = Column(String)
    candidate_email = Column(String)
    job_id          = Column(Integer, ForeignKey("jobs.id"), nullable=True)  # ← fixed
    job_title       = Column(String)
    job_skills      = Column(String, default="")
    github_url      = Column(String, default="")
    github_data     = Column(Text, default="{}")
    eval_summary    = Column(Text, default="")
    scheduled_time  = Column(String, default="")
    transcript      = Column(Text, default="")
    suggestions     = Column(Text, default="[]")
    candidate_score = Column(Integer, nullable=True)
    status          = Column(String, default="pending")
    outcome         = Column(String, default="")
    created_at      = Column(DateTime, default=lambda: datetime.utcnow())
    started_at      = Column(DateTime, nullable=True)
    ended_at        = Column(DateTime, nullable=True)


from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy import JSON as GenericJSON  # For cross-database compatibility

class BgvRecord(Base):
    __tablename__ = "bgv_records"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    application_id = Column(Integer, nullable=False)
    job_id = Column(Integer, nullable=False)
    candidate_name = Column(String, nullable=False)
    candidate_email = Column(String, nullable=False)
    job_title = Column(String, nullable=True)
    
    # Identity documents
    aadhaar_b64 = Column(Text, nullable=True)
    pan_b64 = Column(Text, nullable=True)
    passport_b64 = Column(Text, nullable=True)
    identity_status = Column(String, default="pending")
    identity_confidence = Column(Integer, nullable=True)
    identity_flags = Column(JSON, nullable=True)  # JSON type
    
    # Face verification
    face_match = Column(String, nullable=True)
    face_match_confidence = Column(Integer, nullable=True)
    selfie_b64 = Column(Text, nullable=True)
    
    # Education
    degree_cert_b64 = Column(Text, nullable=True)
    education_status = Column(String, default="pending")
    education_confidence = Column(Integer, nullable=True)
    education_flags = Column(JSON, nullable=True)
    
    # Employment
    experience_letters_b64 = Column(JSON, nullable=True)  # JSON array
    employment_status = Column(String, default="pending")
    employment_confidence = Column(Integer, nullable=True)
    employment_flags = Column(JSON, nullable=True)
    
    # Criminal
    criminal_affidavit_b64 = Column(Text, nullable=True)
    criminal_status = Column(String, default="pending")
    criminal_flags = Column(JSON, nullable=True)
    
    # References
    reference_contacts = Column(JSON, nullable=True)
    reference_status = Column(String, default="pending")
    reference_notes = Column(Text, nullable=True)
    
    # Overall
    overall_status = Column(String, default="pending")
    ai_summary = Column(Text, nullable=True)
    hr_notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.utcnow())
    submitted_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
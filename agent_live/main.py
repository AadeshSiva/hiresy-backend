# agent_livehr/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from groq import Groq
import os
import json
import uuid
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

from agent_common.database import get_db, engine, init_db
from agent_common.models import Base, LiveSession, Job

app = FastAPI(title="Hiersy Live HR Agent")

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

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
JAAS_APP_ID = os.getenv("JAAS_APP_ID")


# ── Request Models ────────────────────────────────────────────────────────────
class CreateLiveHRRequest(BaseModel):
    application_id: int
    job_id: int
    candidate_name: str
    candidate_email: str
    job_title: str
    job_skills: str = ""
    hr_email: str = ""
    github_url: str = ""
    eval_summary: str = ""


class TranscriptChunk(BaseModel):
    transcript: str
    job_role: str
    candidate_name: str
    conversation_history: list[dict] = []


class ProfileData(BaseModel):
    github_url: str | None = None
    leetcode_url: str | None = None
    job_role: str
    candidate_name: str


class AnswerScore(BaseModel):
    question: str
    answer: str
    job_role: str


# ── POST /livehr/create ───────────────────────────────────────────────────────
@app.post("/livehr/create")
async def create_livehr_session(req: CreateLiveHRRequest, db: Session = Depends(get_db)):
    existing = db.query(LiveSession).filter(
        LiveSession.application_id == req.application_id
    ).first()
    if existing:
        return {
            "token": existing.token,
            "meet_url": existing.meet_code,
            "scheduled_time": existing.scheduled_time or "",
            "already_exists": True,
        }

    token = str(uuid.uuid4()).replace("-", "")[:24]
    room = f"hiersy-{token}"
    if JAAS_APP_ID:
        meet_url = f"https://8x8.vc/{JAAS_APP_ID}/{room}"
    else:
        meet_url = f"https://meet.jit.si/{room}"

    scheduled_dt = (
        datetime.now(timezone.utc)
        .replace(hour=10, minute=0, second=0, microsecond=0)
        + timedelta(days=2)
    )
    scheduled_str = scheduled_dt.isoformat()

    suggestions_data = json.dumps({"hr_email": req.hr_email})

    session = LiveSession(
        token=token,
        meet_code=meet_url,
        application_id=req.application_id,
        job_id=req.job_id,              # ← fixed: now saved
        candidate_name=req.candidate_name,
        candidate_email=req.candidate_email,
        job_title=req.job_title,
        job_skills=req.job_skills,
        github_url=req.github_url,
        eval_summary=req.eval_summary,
        scheduled_time=scheduled_str,
        suggestions=suggestions_data,
        status="pending",
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    return {
        "token": token,
        "meet_url": meet_url,
        "scheduled_time": scheduled_str,
        "already_exists": False,
    }


# ── GET /livehr/session/{token} ───────────────────────────────────────────────
@app.get("/livehr/session/{token}")
async def get_livehr_session(token: str, db: Session = Depends(get_db)):
    s = db.query(LiveSession).filter(LiveSession.token == token).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "token": s.token,
        "meet_url": s.meet_code,
        "candidate_name": s.candidate_name,
        "candidate_email": s.candidate_email,
        "job_title": s.job_title,
        "job_skills": s.job_skills or "",
        "github_url": s.github_url or "",
        "eval_summary": s.eval_summary or "",
        "scheduled_time": s.scheduled_time or "",
        "status": s.status,
    }


# ── GET /livehr/sessions?hr_email=... ────────────────────────────────────────
@app.get("/livehr/sessions")
async def get_hr_sessions(hr_email: str, db: Session = Depends(get_db)):
    try:
        results = (
            db.query(LiveSession, Job)
            .join(Job, LiveSession.job_id == Job.id)   # ← fixed: LiveSession not LiveHRSession
            .filter(Job.posted_by == hr_email)
            .order_by(LiveSession.scheduled_time)
            .all()
        )
    except Exception as e:
        print(f"[LiveHR] sessions query failed: {e}")
        return []

    sessions = []
    for s, j in results:
        sessions.append({
            "token": s.token,
            "application_id": s.application_id,
            "candidate_name": s.candidate_name,
            "job_title": s.job_title,
            "meet_url": s.meet_code,
            "scheduled_time": s.scheduled_time if s.scheduled_time else "",  # ← fixed: no .isoformat()
            "status": s.status,
        })
    return sessions


# ── GET /meet/room-url ────────────────────────────────────────────────────────
@app.get("/meet/room-url")
async def get_room_url(interview_id: str):
    if not JAAS_APP_ID:
        return {"meet_url": f"https://meet.jit.si/hiersy-{interview_id}"}
    return {"meet_url": f"https://8x8.vc/{JAAS_APP_ID}/hiersy-{interview_id}"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_username_from_url(url: str) -> str | None:
    if not url:
        return None
    return url.rstrip("/").split("/")[-1] or None


async def fetch_github_profile(github_url: str) -> dict:
    username = extract_username_from_url(github_url)
    if not username:
        return {}
    async with httpx.AsyncClient(timeout=8.0) as http:
        try:
            user_res = await http.get(
                f"https://api.github.com/users/{username}",
                headers={"Accept": "application/vnd.github+json"},
            )
            repos_res = await http.get(
                f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10",
                headers={"Accept": "application/vnd.github+json"},
            )
            user_data = user_res.json() if user_res.status_code == 200 else {}
            repos_data = repos_res.json() if repos_res.status_code == 200 else []
            if not isinstance(repos_data, list):
                repos_data = []
            repos = [
                {
                    "name": r.get("name", ""),
                    "description": r.get("description", "") or "",
                    "language": r.get("language", "") or "",
                    "stars": r.get("stargazers_count", 0),
                    "topics": r.get("topics", []),
                }
                for r in repos_data[:8]
            ]
            languages = list({r["language"] for r in repos if r["language"]})
            return {
                "username": username,
                "bio": user_data.get("bio", "") or "",
                "public_repos": user_data.get("public_repos", 0),
                "languages": languages,
                "repos": repos,
            }
        except Exception as e:
            print(f"GitHub fetch error: {e}")
            return {"username": username}


async def fetch_leetcode_profile(leetcode_url: str) -> dict:
    username = extract_username_from_url(leetcode_url)
    if not username:
        return {}
    async with httpx.AsyncClient(timeout=8.0) as http:
        try:
            res = await http.get(f"https://leetcode-stats-api.herokuapp.com/{username}")
            if res.status_code == 200:
                data = res.json()
                return {
                    "username": username,
                    "total_solved": data.get("totalSolved", 0),
                    "easy_solved": data.get("easySolved", 0),
                    "medium_solved": data.get("mediumSolved", 0),
                    "hard_solved": data.get("hardSolved", 0),
                    "ranking": data.get("ranking", "N/A"),
                    "acceptance_rate": data.get("acceptanceRate", "N/A"),
                }
            return {"username": username}
        except Exception as e:
            print(f"LeetCode fetch error: {e}")
            return {"username": username}


# ── POST /copilot/fetch-questions ─────────────────────────────────────────────
@app.post("/copilot/fetch-questions")
async def fetch_questions_from_profiles(data: ProfileData):
    github_context = ""
    leetcode_context = ""

    if data.github_url:
        gh = await fetch_github_profile(data.github_url)
        if gh:
            repos_str = ", ".join(
                f"{r['name']} ({r['language']}){': ' + r['description'][:60] if r['description'] else ''}"
                for r in gh.get("repos", [])[:5]
            )
            github_context = (
                f"GitHub username: {gh.get('username', '')}\n"
                f"Bio: {gh.get('bio', '')}\n"
                f"Languages used: {', '.join(gh.get('languages', []))}\n"
                f"Top repos: {repos_str}\n"
                f"Public repos count: {gh.get('public_repos', 0)}"
            )

    if data.leetcode_url:
        lc = await fetch_leetcode_profile(data.leetcode_url)
        if lc:
            leetcode_context = (
                f"LeetCode username: {lc.get('username', '')}\n"
                f"Total solved: {lc.get('total_solved', 0)} "
                f"(Easy: {lc.get('easy_solved', 0)}, Medium: {lc.get('medium_solved', 0)}, Hard: {lc.get('hard_solved', 0)})\n"
                f"Ranking: {lc.get('ranking', 'N/A')}\n"
                f"Acceptance rate: {lc.get('acceptance_rate', 'N/A')}%"
            )

    if not github_context and not leetcode_context:
        fallback_prompt = (
            f"You are an expert technical interviewer for a {data.job_role} role. "
            "Generate 6 sharp, role-specific interview questions. "
            "Return ONLY a JSON array of 6 strings. No markdown, no explanation."
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": fallback_prompt},
                {"role": "user", "content": f"Generate 6 interview questions for {data.candidate_name} applying for {data.job_role}."},
            ],
            temperature=0.7,
            max_tokens=500,
        )
        raw = response.choices[0].message.content.strip()
        try:
            return {"questions": json.loads(raw), "source": "role_based"}
        except Exception:
            return {"questions": [raw], "source": "role_based"}

    system_prompt = (
        f"You are an expert technical interviewer for a {data.job_role} role. "
        "Given the candidate's GitHub and LeetCode profiles, generate 6 highly targeted interview questions.\n\n"
        "Rules:\n"
        "- 3 questions must be based on their GitHub repos/languages/projects\n"
        "- 3 questions must be based on their LeetCode stats\n"
        "- Questions must be specific to THEIR profile, not generic\n"
        "- If a profile section is missing, generate smart role-based questions for that section\n"
        "- Return ONLY a JSON array of 6 strings. No markdown, no explanation."
    )
    user_content = f"Candidate: {data.candidate_name}\nRole: {data.job_role}\n\n"
    if github_context:
        user_content += f"GitHub Profile:\n{github_context}\n\n"
    if leetcode_context:
        user_content += f"LeetCode Profile:\n{leetcode_context}"

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.75,
        max_tokens=600,
    )
    raw = response.choices[0].message.content.strip()
    try:
        return {"questions": json.loads(raw), "source": "profile_based"}
    except Exception:
        return {"questions": [raw], "source": "profile_based"}


# ── POST /copilot/live-eval ───────────────────────────────────────────────────
@app.post("/copilot/live-eval")
async def live_eval(data: TranscriptChunk):
    system_prompt = (
        f"You are an expert AI interview evaluator for a {data.job_role} role. "
        "Evaluate the candidate's running answer transcript and return a JSON object.\n\n"
        "Return ONLY this JSON (no markdown, no explanation):\n"
        "{\n"
        '  "overall_score": <1-10 integer>,\n'
        '  "confidence": <0-100 integer based on how confident the candidate sounds>,\n'
        '  "ai_feedback": "<One precise sentence evaluating the answer quality.>"\n'
        "}"
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Candidate transcript so far:\n{data.transcript}"},
        ],
        temperature=0.3,
        max_tokens=250,
    )
    raw = response.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
        return {
            "overall_score": parsed.get("overall_score", 0),
            "confidence": parsed.get("confidence", 0),
            "ai_feedback": parsed.get("ai_feedback", ""),
        }
    except Exception:
        return {"overall_score": 0, "confidence": 0, "ai_feedback": raw}


# ── POST /copilot/suggest ─────────────────────────────────────────────────────
@app.post("/copilot/suggest")
async def suggest_questions(data: TranscriptChunk):
    system_prompt = (
        f"You are an expert HR interview copilot for a {data.job_role} role. "
        "Suggest 3 sharp, relevant follow-up questions based on the conversation so far. "
        "Return ONLY a JSON array of strings. No explanation, no markdown."
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            *data.conversation_history[-6:],
            {"role": "user", "content": f"Transcript:\n{data.transcript}\n\nSuggest 3 follow-up questions."},
        ],
        temperature=0.7,
        max_tokens=300,
    )
    raw = response.choices[0].message.content.strip()
    try:
        return {"questions": json.loads(raw)}
    except Exception:
        return {"questions": [raw]}


# ── POST /copilot/summary ─────────────────────────────────────────────────────
@app.post("/copilot/summary")
async def get_summary(data: TranscriptChunk):
    system_prompt = (
        f"You are an HR interview summarizer for a {data.job_role} role. "
        'Return ONLY a JSON object: {"strengths": "...", "gaps": "...", "pending": "..."}'
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcript:\n{data.transcript}"},
        ],
        temperature=0.4,
        max_tokens=300,
    )
    raw = response.choices[0].message.content.strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"strengths": "", "gaps": "", "pending": raw}


# ── WebSocket Copilot ─────────────────────────────────────────────────────────
@app.websocket("/ws/copilot")
async def websocket_copilot(websocket: WebSocket):
    await websocket.accept()
    transcript_buffer = ""
    try:
        while True:
            data = await websocket.receive_json()
            transcript_buffer += " " + data.get("chunk", "")
            if len(transcript_buffer.split()) > 30:
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                f"Suggest 2 follow-up interview questions for a "
                                f"{data.get('job_role', 'Software Engineer')} role. "
                                "Return JSON array only."
                            ),
                        },
                        {"role": "user", "content": transcript_buffer[-1000:]},
                    ],
                    max_tokens=200,
                )
                raw = response.choices[0].message.content.strip()
                try:
                    questions = json.loads(raw)
                except Exception:
                    questions = [raw]
                await websocket.send_json({"type": "suggestions", "questions": questions})
                transcript_buffer = ""
    except WebSocketDisconnect:
        pass
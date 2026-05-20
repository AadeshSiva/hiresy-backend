from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import uuid, json, requests, re
from datetime import datetime, timezone

from agent_common.config import GROQ_API_KEY, GROQ_URL, GROQ_MODEL, ORCHESTRATOR_URL, FRONTEND_URL
from agent_common.database import get_db, engine, init_db
from agent_common.models import Base, CommSession, SpokenTest
from agent_common.round_router import trigger_next_round

app = FastAPI(title="Hiersy Communication Agent")

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
        print(f"[Comm] round index lookup failed: {e}")
    return 0


def _reject(application_id: int):
    try:
        requests.patch(
            f"{ORCHESTRATOR_URL}/applications/{application_id}/status",
            json={"status": "rejected"}, timeout=5
        )
    except:
        pass


# ── Verbal test (UNCHANGED) ───────────────────────────────────────────────────
class CreateCommRequest(BaseModel):
    application_id: int
    job_id: int
    candidate_name: str
    candidate_email: str
    job_title: str
    test_type: str = "verbal"
    duration_mins: int = 10
    total_questions: int = 5
    pass_score: int = 60


class SubmitCommRequest(BaseModel):
    answers: List[int]


def generate_verbal_questions(job_title: str, count: int = 5) -> list:
    try:
        prompt = (
            f"Generate {count} verbal ability questions (synonyms, antonyms, grammar, vocabulary) "
            f"for a '{job_title}' role. Each question must have 4 options and one correct answer (0-indexed). "
            f"Return ONLY JSON array: [{{\"question\":\"...\",\"options\":[\"A\",\"B\",\"C\",\"D\"],\"correct\":0}}]"
        )
        res = requests.post(
            GROQ_URL,
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.5},
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=30
        )
        res.raise_for_status()
        raw = res.json()["choices"][0]["message"]["content"]
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        s = clean.find("["); e = clean.rfind("]")
        if s == -1 or e == -1:
            raise ValueError("No JSON array")
        return json.loads(clean[s:e+1])[:count]
    except Exception as ex:
        print(f"Groq error, using static questions: {ex}")
        return [
            {"question": "Choose the synonym of 'Abundant'.", "options": ["Scarce","Plentiful","Empty","Rare"], "correct": 1},
            {"question": "Antonym of 'Benevolent'?", "options": ["Kind","Malicious","Charitable","Generous"], "correct": 1},
            {"question": "Correct sentence:", "options": ["He go to school.","He goes to school.","He going to school.","He gone to school."], "correct": 1},
            {"question": "Meaning of 'Ephemeral'?", "options": ["Permanent","Short-lived","Powerful","Weak"], "correct": 1},
            {"question": "Correct spelling?", "options": ["Accomodate","Acommodate","Accommodate","Acomodate"], "correct": 2},
        ][:count]


@app.post("/comm/create")
def create_comm_test(req: CreateCommRequest, db: Session = Depends(get_db)):
    existing = db.query(CommSession).filter(CommSession.application_id == req.application_id).first()
    if existing:
        return {"token": existing.token, "already_exists": True}
    questions = generate_verbal_questions(req.job_title, req.total_questions)
    token = str(uuid.uuid4()).replace("-", "")[:12]
    test = CommSession(
        token=token, application_id=req.application_id, job_id=req.job_id,
        candidate_name=req.candidate_name, candidate_email=req.candidate_email,
        job_title=req.job_title, test_type=req.test_type,
        questions_json=json.dumps(questions), duration_mins=req.duration_mins,
        total_questions=len(questions), pass_score=req.pass_score, status="pending",
        email_sent=True,
    )
    db.add(test); db.commit(); db.refresh(test)
    return {"token": token}


@app.get("/comm-test/{token}")
def get_comm_test(token: str, db: Session = Depends(get_db)):
    test = db.query(CommSession).filter(CommSession.token == token).first()
    if not test:
        raise HTTPException(404, "Test not found")
    if test.status == "submitted":
        return {"status": "submitted", "score_pct": test.score_pct, "passed": test.passed,
                "candidate_name": test.candidate_name, "job_title": test.job_title}
    questions = json.loads(test.questions_json)
    safe_qs = [{"question": q["question"], "options": q["options"]} for q in questions]
    return {
        "token": token, "candidate_name": test.candidate_name, "job_title": test.job_title,
        "duration_mins": test.duration_mins, "total_questions": test.total_questions,
        "pass_score": test.pass_score, "status": test.status,
        "questions": safe_qs, "test_type": test.test_type,
    }


@app.post("/comm-test/{token}/start")
def start_comm_test(token: str, db: Session = Depends(get_db)):
    test = db.query(CommSession).filter(CommSession.token == token).first()
    if not test:
        raise HTTPException(404)
    if test.status == "submitted":
        raise HTTPException(400, "Already submitted")
    if test.status != "started":
        test.status = "started"; test.started_at = datetime.now(timezone.utc); db.commit()
    return {"started_at": str(test.started_at), "duration_mins": test.duration_mins}


@app.post("/comm-test/{token}/submit")
def submit_comm_test(token: str, req: SubmitCommRequest, db: Session = Depends(get_db)):
    test = db.query(CommSession).filter(CommSession.token == token).first()
    if not test:
        raise HTTPException(404)
    if test.status == "submitted":
        raise HTTPException(400, "Already submitted")

    questions = json.loads(test.questions_json)
    correct = sum(1 for i, q in enumerate(questions)
                  if i < len(req.answers) and req.answers[i] == q["correct"])
    total = len(questions)
    pct = round((correct / total) * 100)
    passed = pct >= test.pass_score

    test.answers_json = json.dumps(req.answers)
    test.score = correct; test.score_pct = pct; test.passed = passed
    test.status = "submitted"; test.submitted_at = datetime.now(timezone.utc)
    db.commit()

    if passed:
        current_idx = _get_round_index(test.application_id, test.job_id)
        try:
            trigger_next_round(application_id=test.application_id, current_round_index=current_idx)
        except Exception as e:
            print(f"[Comm] Router error: {e}")
    else:
        _reject(test.application_id)

    return {"passed": passed, "score_pct": pct, "score": correct, "total": total}


# ════════════════════════════════════════════════════════════════════════════════
# ── Spoken English test (REFACTORED) ─────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

# ── Scoring constants ─────────────────────────────────────────────────────────
LISTEN_SELECT_POINTS = 10   # per question (2 questions → max 20)
REPEAT_MAX_POINTS    = 10   # per question (2 questions → max 20)
READ_ALOUD_MAX       = 30   # single paragraph
TOTAL_MAX            = 70   # 20 + 20 + 30
PASS_THRESHOLD       = 50   # minimum score to pass


# ── Content generation ─────────────────────────────────────────────────────────
def _generate_spoken_content(job_title: str) -> dict:
    """
    Ask Groq to produce:
      - 2 listen-select sentences, each with 4 MCQ options and a correct answer index
      - 2 short sentences for the repeat-sentence section
      - 1 paragraph (60–90 words) for the read-aloud section

    Returns a dict matching the shape stored in SpokenTest.content.
    Falls back to static content on any error.
    """
    prompt = f"""You are generating content for a Spoken English test for a '{job_title}' role.
Return ONLY a JSON object with this exact structure (no markdown fences):
{{
  "listen_select_questions": [
    {{
      "sentence": "A clear spoken sentence about work or communication.",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct": 0
    }},
    {{
      "sentence": "Another clear spoken sentence.",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct": 2
    }}
  ],
  "repeat_sentences": [
    "A short, natural English sentence the candidate must repeat.",
    "Another short sentence for repetition practice."
  ],
  "read_aloud_paragraph": "A 60-90 word paragraph relevant to professional communication or the role."
}}
Make sure all sentences are grammatically correct, professional, and appropriate for a {job_title} candidate."""

    try:
        res = requests.post(
            GROQ_URL,
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.6},
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            timeout=40,
        )
        res.raise_for_status()
        raw = res.json()["choices"][0]["message"]["content"]
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        s = clean.find("{"); e = clean.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("No JSON object found")
        return json.loads(clean[s:e+1])
    except Exception as ex:
        print(f"[Spoken] Content generation failed, using fallback: {ex}")
        return {
            "listen_select_questions": [
                {
                    "sentence": "The meeting has been rescheduled to Thursday afternoon.",
                    "options": [
                        "The meeting is cancelled.",
                        "The meeting is now on Thursday afternoon.",
                        "The meeting was held on Thursday morning.",
                        "Thursday's meeting was confirmed yesterday.",
                    ],
                    "correct": 1,
                },
                {
                    "sentence": "Please submit the report before the end of the business day.",
                    "options": [
                        "Submit the report after business hours.",
                        "The report deadline is next week.",
                        "Submit the report before the working day ends.",
                        "The report should be reviewed by business partners.",
                    ],
                    "correct": 2,
                },
            ],
            "repeat_sentences": [
                "Effective communication is key to professional success.",
                "Please send me the updated project timeline by Friday.",
            ],
            "read_aloud_paragraph": (
                "In today's fast-paced professional environment, clear and concise communication "
                "is more important than ever. Whether you are writing an email, presenting to a "
                "client, or collaborating with your team, the ability to express your ideas "
                "confidently and accurately sets you apart. Strong communicators listen actively, "
                "respond thoughtfully, and adapt their tone to the audience. These skills are "
                "essential for building trust and achieving results in any workplace."
            ),
        }


# ── Whisper transcription helper ──────────────────────────────────────────────
def _transcribe(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": (filename, audio_bytes, "audio/webm")}
    data = {"model": "whisper-large-v3", "language": "en"}
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers=headers, files=files, data=data, timeout=40,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Whisper transcription failed: {resp.text}")
    return resp.json().get("text", "").strip()


# ── Word-overlap similarity (simple ROUGE-L proxy) ────────────────────────────
def _word_similarity(reference: str, hypothesis: str) -> float:
    """Returns a 0.0–1.0 score based on word overlap."""
    ref_words  = reference.lower().split()
    hyp_words  = hypothesis.lower().split()
    if not ref_words:
        return 0.0
    # count how many reference words appear in hypothesis
    hyp_set = set(hyp_words)
    matches = sum(1 for w in ref_words if w in hyp_set)
    return matches / len(ref_words)


def _repeat_score(similarity: float) -> int:
    """Convert similarity ratio to points (0, 5, or 10)."""
    if similarity >= 0.80:
        return REPEAT_MAX_POINTS
    elif similarity >= 0.50:
        return 5
    return 0


# ── Read-aloud evaluation via Groq ────────────────────────────────────────────
def _evaluate_read_aloud(paragraph: str, transcript: str) -> dict:
    """Returns {"score": int 0-30, "feedback": str}."""
    prompt = f"""You are evaluating a candidate's Read Aloud spoken English performance.

Original paragraph:
\"\"\"{paragraph}\"\"\"

Candidate's transcript (from speech recognition):
\"\"\"{transcript}\"\"\"

Evaluate the candidate on:
1. Pronunciation accuracy (how closely words match the original)
2. Fluency (smooth, natural delivery without excessive hesitation)
3. Completeness (did they read the full paragraph?)

Score from 0 to 30 where:
- 25-30: Excellent – near-perfect pronunciation, very fluent, complete
- 18-24: Good – minor errors, mostly fluent, mostly complete
- 10-17: Fair – noticeable errors, some disfluency or omissions
- 0-9:   Poor – many errors, poor fluency, or very incomplete

Return ONLY JSON (no markdown): {{"score": <int>, "feedback": "<one sentence summary>"}}"""

    try:
        res = requests.post(
            GROQ_URL,
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            timeout=30,
        )
        res.raise_for_status()
        raw = res.json()["choices"][0]["message"]["content"]
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        score = max(0, min(READ_ALOUD_MAX, int(data.get("score", 0))))
        return {"score": score, "feedback": data.get("feedback", "")}
    except Exception as ex:
        print(f"[Spoken] Read-aloud eval failed: {ex}")
        return {"score": 15, "feedback": "Evaluation could not be completed."}


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

class CreateSpokenRequest(BaseModel):
    application_id: int
    job_id: int
    candidate_name: str
    candidate_email: str
    job_title: str


@app.post("/spoken/create")
def create_spoken_test(req: CreateSpokenRequest, db: Session = Depends(get_db)):
    existing = db.query(SpokenTest).filter(
        SpokenTest.application_id == req.application_id
    ).first()
    if existing:
        return {"token": existing.token, "already_exists": True}

    content = _generate_spoken_content(req.job_title)
    token = str(uuid.uuid4()).replace("-", "")[:12]

    test = SpokenTest(
        token=token,
        application_id=req.application_id,
        job_id=req.job_id,
        candidate_name=req.candidate_name,
        candidate_email=req.candidate_email,
        job_title=req.job_title,
        # Store generated content in `paragraph` (repurposed as JSON) and `topic`
        # For a clean solution, this goes into a `content` column (see migration note below).
        # We serialise the full content dict into `paragraph` as JSON since that column
        # already exists and `topic` is unused in the new flow.
        paragraph=json.dumps(content),
        topic="",   # no longer used; kept for model compatibility
        status="pending",
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    # Email is sent by round_router before this call.
    return {"token": token}


@app.get("/spoken/{token}")
def get_spoken_test(token: str, db: Session = Depends(get_db)):
    test = db.query(SpokenTest).filter(SpokenTest.token == token).first()
    if not test:
        raise HTTPException(404, "Spoken test not found")

    if test.status == "submitted":
        # Do NOT expose score to the frontend – only a completion status.
        return {
            "status": "submitted",
            "candidate_name": test.candidate_name,
            "job_title": test.job_title,
        }

    # Deserialise content (stored as JSON in `paragraph` column)
    try:
        content = json.loads(test.paragraph)
    except Exception:
        # Fallback if content is not JSON (shouldn't happen after migration)
        content = _generate_spoken_content(test.job_title)

    # Strip correct answers before sending to the frontend
    safe_ls_questions = [
        {"sentence": q["sentence"], "options": q["options"]}
        for q in content.get("listen_select_questions", [])
    ]

    return {
        "token": test.token,
        "candidate_name": test.candidate_name,
        "job_title": test.job_title,
        "status": test.status,
        "listen_select_questions": safe_ls_questions,
        "repeat_sentences": content.get("repeat_sentences", []),
        "read_aloud_paragraph": content.get("read_aloud_paragraph", ""),
    }


@app.post("/spoken/{token}/submit")
async def submit_spoken_test(
    token: str,
    listen_select_answers: str = Form(...),   # JSON-encoded array, e.g. [1, 2]
    repeat_audio_0: UploadFile = File(...),
    repeat_audio_1: UploadFile = File(...),
    read_aloud_audio: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    test = db.query(SpokenTest).filter(SpokenTest.token == token).first()
    if not test:
        raise HTTPException(404, "Spoken test not found")
    if test.status == "submitted":
        raise HTTPException(400, "Already submitted")

    # ── Deserialise stored content ──────────────────────────────────────────
    try:
        content = json.loads(test.paragraph)
    except Exception:
        raise HTTPException(500, "Test content could not be loaded")

    ls_questions   = content.get("listen_select_questions", [])
    repeat_sents   = content.get("repeat_sentences", [])
    read_paragraph = content.get("read_aloud_paragraph", "")

    # ── Section 1: Listen & Select scoring ─────────────────────────────────
    try:
        candidate_ls = json.loads(listen_select_answers)
    except Exception:
        candidate_ls = []

    ls_score = 0
    ls_details = []
    for i, q in enumerate(ls_questions):
        candidate_choice = candidate_ls[i] if i < len(candidate_ls) else -1
        correct = q.get("correct", -1)
        earned  = LISTEN_SELECT_POINTS if candidate_choice == correct else 0
        ls_score += earned
        ls_details.append({
            "question_idx": i,
            "candidate_choice": candidate_choice,
            "correct_choice": correct,
            "points": earned,
        })

    # ── Section 2: Repeat Sentence scoring ─────────────────────────────────
    repeat_score = 0
    repeat_details = []

    repeat_files = [repeat_audio_0, repeat_audio_1]
    for i, upload in enumerate(repeat_files):
        if i >= len(repeat_sents):
            break
        reference = repeat_sents[i]
        try:
            audio_bytes = await upload.read()
            transcript  = _transcribe(audio_bytes, f"repeat_{i}.webm")
            similarity  = _word_similarity(reference, transcript)
            points      = _repeat_score(similarity)
        except Exception as ex:
            print(f"[Spoken] Repeat {i} transcription error: {ex}")
            transcript = ""; similarity = 0.0; points = 0

        repeat_score += points
        repeat_details.append({
            "question_idx": i,
            "reference": reference,
            "transcript": transcript,
            "similarity": round(similarity, 3),
            "points": points,
        })

    # ── Section 3: Read Aloud scoring ──────────────────────────────────────
    try:
        ra_bytes      = await read_aloud_audio.read()
        ra_transcript = _transcribe(ra_bytes, "read_aloud.webm")
        ra_eval       = _evaluate_read_aloud(read_paragraph, ra_transcript)
        ra_score      = ra_eval["score"]
        ra_feedback   = ra_eval["feedback"]
    except Exception as ex:
        print(f"[Spoken] Read-aloud error: {ex}")
        ra_transcript = ""; ra_score = 0; ra_feedback = "Evaluation error."

    # ── Totals ──────────────────────────────────────────────────────────────
    total_score = ls_score + repeat_score + ra_score
    passed      = total_score >= PASS_THRESHOLD

    # ── Persist ─────────────────────────────────────────────────────────────
    evaluation_payload = {
        "listen_select": {"score": ls_score, "max": 20, "details": ls_details},
        "repeat_sentence": {"score": repeat_score, "max": 20, "details": repeat_details},
        "read_aloud": {
            "score": ra_score,
            "max": READ_ALOUD_MAX,
            "feedback": ra_feedback,
            "transcript": ra_transcript,
        },
        "total": total_score,
        "max_total": TOTAL_MAX,
        "passed": passed,
    }

    test.score      = total_score
    test.passed     = passed
    test.transcript = ra_transcript          # primary transcript for HR review
    test.evaluation = json.dumps(evaluation_payload)
    test.status     = "submitted"
    test.submitted_at = datetime.now(timezone.utc)
    db.commit()

    # ── Trigger next round or reject ────────────────────────────────────────
    if passed:
        current_idx = _get_round_index(test.application_id, test.job_id)
        try:
            trigger_next_round(application_id=test.application_id, current_round_index=current_idx)
        except Exception as e:
            print(f"[Spoken] Router error: {e}")
    else:
        _reject(test.application_id)

    # Do NOT return score to the frontend – only a success acknowledgement.
    return {"success": True}
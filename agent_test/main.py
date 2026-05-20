#agent_test/main.py
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import os, json, requests, uuid, re
from datetime import datetime, timezone

from agent_common.config import (
    GROQ_API_KEY, GROQ_URL, GROQ_MODEL,
    ORCHESTRATOR_URL, FRONTEND_URL,
)
from agent_common.database import get_db, engine, init_db
from agent_common.models import Base, TestSession
from agent_common.round_router import trigger_next_round

app = FastAPI(title="Hiersy Test Agent")

@app.on_event("startup")
def startup_event():
    init_db()

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["http://localhost:5173", "https://hiresyai.vercel.app"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)


# ── JSON repair — fixes unescaped quotes inside string values ─────────────────
def _repair_json(raw: str) -> str:
    """
    Repair common Groq JSON issues:
    1. Strip markdown fences
    2. Remove control characters (except valid JSON escapes)
    3. Fix unescaped double quotes inside string values by walking char-by-char
    """
    # Step 1: strip fences
    s = raw.strip()
    s = re.sub(r"```json\s*", "", s)
    s = re.sub(r"```\s*", "", s)
    s = s.strip()

    # Step 2: remove control chars that are NOT part of a JSON escape sequence
    # Keep \n \r \t \b \f \" \\ (i.e. backslash followed by n r t b f " \)
    s = re.sub(r"[\x00-\x1F\x7F](?![nrtbf\"\\])", "", s)

    # Step 3: fix unescaped double quotes inside string values
    # Walk character by character tracking parser state
    result = []
    in_string = False
    i = 0
    while i < len(s):
        ch = s[i]

        if in_string:
            if ch == '\\':
                # Escape sequence — copy both chars and skip ahead
                result.append(ch)
                i += 1
                if i < len(s):
                    result.append(s[i])
                i += 1
                continue
            elif ch == '"':
                # This closes the string — but is it really the end?
                # Look ahead: after optional whitespace, a valid JSON delimiter
                # must follow: , ] } or : (for keys)
                j = i + 1
                while j < len(s) and s[j] in ' \t\r\n':
                    j += 1
                next_ch = s[j] if j < len(s) else ''
                if next_ch in (',', ']', '}', ':'):
                    # Legitimate string end
                    result.append(ch)
                    in_string = False
                else:
                    # Unescaped quote inside string — escape it
                    result.append('\\')
                    result.append('"')
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
                result.append(ch)
            else:
                result.append(ch)

        i += 1

    return ''.join(result)


def _extract_json_array(raw: str) -> list:
    fixed = _repair_json(raw)
    s = fixed.find("[")
    e = fixed.rfind("]")
    if s == -1 or e == -1:
        raise ValueError("No JSON array found in response")
    return json.loads(fixed[s:e + 1])


def _extract_json_object(raw: str) -> dict:
    fixed = _repair_json(raw)
    s = fixed.find("{")
    e = fixed.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("No JSON object found in response")
    return json.loads(fixed[s:e + 1])


def _safe_json_loads(raw: str):
    return json.loads(_repair_json(raw))


def _deep_clean(obj):
    """Recursively strip any remaining control characters from all strings."""
    if isinstance(obj, str):
        return re.sub(r"[\x00-\x1F\x7F](?![nrtbf\"\\])", "", obj)
    if isinstance(obj, list):
        return [_deep_clean(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _deep_clean(v) for k, v in obj.items()}
    return obj


# ── Retry wrapper ─────────────────────────────────────────────────────────────
def _generate_with_retry(fn, *args, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            return fn(*args)
        except Exception as e:
            last_err = e
            print(f"[Test] Generation attempt {attempt + 1} failed: {e}")
    raise last_err


# ── Round index helper ────────────────────────────────────────────────────────
def _get_round_index(application_id: int, job_id: int) -> int:
    try:
        job_res = requests.get(f"{ORCHESTRATOR_URL}/jobs/{job_id}", timeout=5)
        if job_res.ok:
            rounds_str = job_res.json().get("rounds") or ""
            rounds = [r.strip() for r in rounds_str.split(",") if r.strip()]
            app_res = requests.get(f"{ORCHESTRATOR_URL}/application/{application_id}", timeout=5)
            if app_res.ok:
                status = app_res.json().get("status", "round_1")
                m = re.match(r"round_(\d+)", status or "")
                if m:
                    return int(m.group(1)) - 1
    except Exception as e:
        print(f"[Test] round index lookup failed: {e}")
    return 0


# ------------------- Request Models -------------------
class CreateTestRequest(BaseModel):
    application_id: int
    job_id: int
    candidate_name: str
    candidate_email: str
    job_title: str
    job_skills: str
    test_type: str = "mcq"
    duration_mins: int = 20
    total_questions: int = 10
    pass_score: int = 60

class SubmitAnswersRequest(BaseModel):
    answers: Optional[List[int]] = None
    code: Optional[str] = None

class RunCodeRequest(BaseModel):
    code: str

class ChatRequest(BaseModel):
    message: str
    context: dict = {}

class EditRequest(BaseModel):
    instruction: str
    code: str

class ReviewRequest(BaseModel):
    passed: bool
    feedback: Optional[str] = ""


# ------------------- Question Generators -------------------
def generate_mcq_questions(job_title: str, skills: str, count: int) -> list:
    prompt = (
        f'You are a technical interviewer. Generate exactly {count} multiple-choice questions '
        f'for the role "{job_title}". Skills to test: {skills}\n\n'
        'Rules:\n'
        '- Mix: 40% easy, 40% medium, 20% hard\n'
        '- Each question has exactly 4 options\n'
        '- Exactly one correct answer (0-indexed: 0=A, 1=B, 2=C, 3=D)\n'
        '- Practical job-relevant questions only\n'
        '- CRITICAL: Never use double quotes inside question text or option text. Use single quotes if needed.\n'
        '- CRITICAL: No newlines, tabs, or special characters inside any string value.\n\n'
        'Return ONLY a valid JSON array, no markdown, no extra text:\n'
        '[{"question":"What does X do?","options":["Option A","Option B","Option C","Option D"],'
        '"correct":0,"explanation":"Brief explanation","skill":"Python","difficulty":"easy"}]'
    )
    res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 5000},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=60,
    )
    res.raise_for_status()
    raw = res.json()["choices"][0]["message"]["content"]
    questions = _extract_json_array(raw)
    questions = _deep_clean(questions)
    valid = [
        q for q in questions
        if isinstance(q, dict)
        and all(k in q for k in ["question", "options", "correct"])
        and isinstance(q["options"], list) and len(q["options"]) == 4
        and 0 <= int(q["correct"]) <= 3
    ]
    if not valid:
        raise Exception("No valid questions generated")
    return valid[:count]


def generate_aptitude_questions(job_title: str, skills: str, count: int) -> list:
    prompt = (
        f'Generate exactly {count} aptitude / logical reasoning multiple-choice questions '
        f'for a candidate applying for "{job_title}". The role requires skills: {skills}\n\n'
        'Each question must have 4 options and exactly one correct answer (0-indexed).\n'
        'Focus on: numerical reasoning, pattern recognition, logical deduction, data interpretation.\n'
        '- CRITICAL: Never use double quotes inside question text or option text. Use single quotes if needed.\n'
        '- CRITICAL: No newlines, tabs, or special characters inside any string value.\n\n'
        'Return ONLY a valid JSON array, no markdown:\n'
        '[{"question":"...","options":["A","B","C","D"],"correct":0,'
        '"explanation":"...","skill":"Logical","difficulty":"medium"}]'
    )
    res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 5000},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=60,
    )
    res.raise_for_status()
    raw = res.json()["choices"][0]["message"]["content"]
    questions = _extract_json_array(raw)
    questions = _deep_clean(questions)
    valid = [
        q for q in questions
        if isinstance(q, dict)
        and all(k in q for k in ["question", "options", "correct"])
        and isinstance(q["options"], list) and len(q["options"]) == 4
        and 0 <= int(q["correct"]) <= 3
    ]
    if not valid:
        raise Exception("No valid aptitude questions generated")
    return valid[:count]


def generate_vibe_coding_problem(job_title: str, skills: str) -> dict:
    prompt = (
        f'Generate a coding problem for a "{job_title}" role. '
        f'Required skills: {skills}. Solvable in 20-30 minutes.\n\n'
        'STRICT RULES:\n'
        '- "problem" must be detailed: include context, constraints, what to return, and edge cases. At least 4-6 sentences. Plain text only, no markdown, no asterisks, no backticks.\n'
        '- "constraints" is a plain text string listing input size limits, value ranges, and assumptions.\n'
        '- "starter_code" must be a real Python function with a descriptive name matching the problem, a docstring explaining parameters and return value, and type hints.\n'
        '- "examples" must have 2-3 entries with realistic input/output values as plain strings.\n'
        '- Return ONLY raw JSON. No markdown fences, no ```json, no ``` anywhere.\n\n'
        'Example of the exact shape to return:\n'
        '{"problem":"You are given a list of integers and a target integer. Write a function that finds two numbers in the list that add up to the target and returns their indices. Each input will have exactly one valid answer. You cannot use the same element twice. If no solution exists return an empty list. Consider both positive and negative numbers in your solution.","constraints":"2 <= len(nums) <= 10000, -10^9 <= nums[i] <= 10^9, target is always an integer","examples":[{"input":"nums = [2, 7, 11, 15], target = 9","output":"[0, 1]"},{"input":"nums = [3, 2, 4], target = 6","output":"[1, 2]"},{"input":"nums = [1, 5, 3, 2], target = 8","output":"[1, 2]"}],"starter_code":"def two_sum(nums: list[int], target: int) -> list[int]:\n    \"\"\"\n    Find two numbers that add up to target.\n\n    Args:\n        nums: List of integers to search through.\n        target: The target sum to find.\n\n    Returns:\n        List containing the two indices [i, j] where nums[i] + nums[j] == target.\n    \"\"\"\n    pass","languages":["python","javascript"]}'
    )
    res = requests.post(
        GROQ_URL,
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2000,
        },
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=60,
    )
    res.raise_for_status()
    raw = res.json()["choices"][0]["message"]["content"]
    problem_data = _extract_json_object(raw)
    problem_data = _deep_clean(problem_data)

    def strip_md(text: str) -> str:
        if not isinstance(text, str):
            return text
        text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
        text = re.sub(r'`{1,3}(.*?)`{1,3}', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        return text.strip()

    problem_data["problem"] = strip_md(problem_data.get("problem", ""))
    problem_data["constraints"] = strip_md(problem_data.get("constraints", ""))

    starter = problem_data.get("starter_code", "")
    if not starter or "def " not in starter:
        problem_data["starter_code"] = (
            "def solution(*args):\n"
            '    """\n'
            "    Write your solution here.\n\n"
            "    Args: refer to the problem statement above.\n"
            '    """\n'
            "    pass"
        )

    for ex in problem_data.get("examples", []):
        if isinstance(ex, dict):
            ex["input"] = strip_md(ex.get("input", ""))
            ex["output"] = strip_md(ex.get("output", ""))

    return problem_data


# ------------------- Health & Create -------------------
@app.get("/health")
def health():
    return {"status": "ok", "groq_configured": bool(GROQ_API_KEY)}


@app.post("/tests/create")
def create_test(req: CreateTestRequest, db: Session = Depends(get_db)):
    existing = db.query(TestSession).filter(TestSession.application_id == req.application_id).first()
    if existing:
        return {
            "id": existing.id, "token": existing.token,
            "already_exists": True, "email_sent": existing.email_sent,
            "test_type": existing.test_type,
        }

    try:
        if req.test_type == "mcq":
            questions = _generate_with_retry(generate_mcq_questions, req.job_title, req.job_skills, req.total_questions)
            questions_json = json.dumps(questions, ensure_ascii=True)
            total_q = len(questions)
        elif req.test_type == "aptitude":
            questions = _generate_with_retry(generate_aptitude_questions, req.job_title, req.job_skills, req.total_questions)
            questions_json = json.dumps(questions, ensure_ascii=True)
            total_q = len(questions)
        elif req.test_type == "vibe_coding":
            problem_data = _generate_with_retry(generate_vibe_coding_problem, req.job_title, req.job_skills)
            questions_json = json.dumps(problem_data, ensure_ascii=True)
            total_q = 1
        else:
            raise HTTPException(400, f"Unknown test_type: {req.test_type}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Test] Question generation failed after retries: {e}")
        raise HTTPException(500, f"Question generation failed: {str(e)}")

    token = str(uuid.uuid4()).replace("-", "")[:24]
    test = TestSession(
        token=token, application_id=req.application_id, job_id=req.job_id,
        candidate_name=req.candidate_name, candidate_email=req.candidate_email,
        job_title=req.job_title, job_skills=req.job_skills,
        questions_json=questions_json, duration_mins=req.duration_mins,
        total_questions=total_q, pass_score=req.pass_score,
        test_type=req.test_type, status="pending",
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    test.email_sent = True
    db.commit()

    return {
        "id": test.id, "token": token,
        "already_exists": False, "email_sent": True,
        "test_type": test.test_type,
    }


# ------------------- Test Access -------------------
@app.get("/test/{token}")
def get_test(token: str, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.token == token).first()
    if not t:
        raise HTTPException(404, "Test not found")
    if t.status == "submitted":
        return {
            "status": "submitted", "score_pct": t.score_pct, "passed": t.passed,
            "score": t.score, "total": t.total_questions, "candidate_name": t.candidate_name,
            "job_title": t.job_title, "pass_score": t.pass_score, "test_type": t.test_type,
        }
    if t.status in ("pending_review", "evaluating"):
        return {
            "status": t.status, "candidate_name": t.candidate_name,
            "job_title": t.job_title, "test_type": t.test_type,
        }
    if t.test_type in ("mcq", "aptitude"):
        questions = json.loads(t.questions_json)
        safe_qs = [{"question": q["question"], "options": q["options"],
                    "skill": q.get("skill", ""), "difficulty": q.get("difficulty", "medium")}
                   for q in questions]
        return {
            "token": token, "candidate_name": t.candidate_name, "job_title": t.job_title,
            "duration_mins": t.duration_mins, "total_questions": t.total_questions,
            "pass_score": t.pass_score, "status": t.status, "questions": safe_qs,
            "test_type": t.test_type,
        }
    else:  # vibe_coding
        problem_data = json.loads(t.questions_json)
        return {
            "token": token, "candidate_name": t.candidate_name, "job_title": t.job_title,
            "duration_mins": t.duration_mins, "total_questions": 1,
            "pass_score": t.pass_score, "status": t.status, "test_type": "vibe_coding",
            "problem_statement": problem_data.get("problem", ""),
            "starter_code": problem_data.get("starter_code", ""),
            "examples": problem_data.get("examples", []),
            "languages": problem_data.get("languages", ["python"]),
        }


@app.post("/test/{token}/start")
def start_test(token: str, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.token == token).first()
    if not t:
        raise HTTPException(404, "Test not found")
    if t.status in ("submitted", "pending_review", "evaluating"):
        raise HTTPException(400, "Test already completed")
    if t.status != "started":
        t.status = "started"
        t.started_at = datetime.now(timezone.utc)
        db.commit()
    return {"started_at": str(t.started_at), "duration_mins": t.duration_mins}


# ------------------- Submit -------------------
@app.post("/test/{token}/submit")
def submit_test(token: str, req: SubmitAnswersRequest, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.token == token).first()
    if not t:
        raise HTTPException(404, "Test not found")
    if t.status in ("submitted", "pending_review"):
        raise HTTPException(400, "Already submitted")

    if t.test_type in ("mcq", "aptitude"):
        if req.answers is None:
            raise HTTPException(400, "Answers required")
        questions = json.loads(t.questions_json)

        enriched_answers = []
        for i, q in enumerate(questions):
            selected = req.answers[i] if i < len(req.answers) else None
            is_correct = (selected is not None and int(selected) == int(q["correct"]))
            enriched_answers.append({
                "question": q.get("question", ""),
                "options": q.get("options", []),
                "selected": selected,
                "correct": int(q["correct"]),
                "is_correct": is_correct,
                "skill": q.get("skill", ""),
                "difficulty": q.get("difficulty", "medium"),
            })

        correct = sum(1 for a in enriched_answers if a["is_correct"])
        total = len(questions)
        pct = round((correct / total) * 100)
        passed = pct >= t.pass_score

        t.answers_json = json.dumps(enriched_answers, ensure_ascii=True)
        t.score = correct
        t.score_pct = pct
        t.passed = passed
        t.status = "submitted"
        t.submitted_at = datetime.now(timezone.utc)
        db.commit()

        if passed:
            current_idx = _get_round_index(t.application_id, t.job_id)
            try:
                trigger_next_round(application_id=t.application_id, current_round_index=current_idx)
            except Exception as e:
                print(f"[Test] Router error: {e}")
        else:
            try:
                requests.patch(
                    f"{ORCHESTRATOR_URL}/applications/{t.application_id}/status",
                    json={"status": "rejected"}, timeout=5,
                )
            except:
                pass

        return {
            "score": correct, "total": total, "score_pct": pct,
            "passed": passed, "pass_score": t.pass_score, "test_type": t.test_type,
        }

    elif t.test_type == "vibe_coding":
        if req.code is None:
            raise HTTPException(400, "Code required")

        t.submission_data = json.dumps({"code": req.code, "submitted_at": str(datetime.now(timezone.utc))})
        t.status = "evaluating"
        t.submitted_at = datetime.now(timezone.utc)
        db.commit()

        problem_data = json.loads(t.questions_json)
        problem = problem_data.get("problem", "")
        examples = problem_data.get("examples", [])

        eval_prompt = (
            f"You are a strict technical interviewer evaluating a candidate's code submission.\n\n"
            f"Problem:\n{problem}\n\n"
            f"Expected examples (input -> output):\n{json.dumps(examples, indent=2) if examples else 'No examples provided.'}\n\n"
            f"Candidate's submitted code:\n{req.code}\n\n"
            "Evaluate the code on correctness, code quality, edge cases, and efficiency.\n"
            "Score 0-100. Pass threshold is 60.\n\n"
            "Return ONLY valid JSON, no markdown:\n"
            '{"score": 75, "passed": true, "feedback": "2-3 sentence evaluation.", "reasoning": "what worked and what did not"}'
        )

        score, passed, feedback, reasoning = 0, False, "Evaluation failed.", ""
        try:
            groq_res = requests.post(
                GROQ_URL,
                json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": eval_prompt}], "temperature": 0},
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                timeout=40,
            )
            groq_res.raise_for_status()
            raw = groq_res.json()["choices"][0]["message"]["content"]
            eval_data = _extract_json_object(raw)
            eval_data = _deep_clean(eval_data)
            score = int(eval_data.get("score", 0))
            passed = score >= t.pass_score
            feedback = eval_data.get("feedback", "")
            reasoning = eval_data.get("reasoning", "")
        except Exception as ex:
            print(f"[VibeCoding] Groq eval failed: {ex}")

        t.score = score
        t.score_pct = float(score)
        t.passed = passed
        t.answers_json = json.dumps({"feedback": feedback, "reasoning": reasoning, "score": score})
        t.status = "submitted"
        db.commit()

        if passed:
            current_idx = _get_round_index(t.application_id, t.job_id)
            try:
                trigger_next_round(application_id=t.application_id, current_round_index=current_idx)
            except Exception as ex:
                print(f"[VibeCoding] Router error: {ex}")
        else:
            try:
                requests.patch(
                    f"{ORCHESTRATOR_URL}/applications/{t.application_id}/status",
                    json={"status": "rejected"}, timeout=5,
                )
            except:
                pass

        return {
            "status": "submitted", "score": score, "score_pct": score,
            "passed": passed, "pass_score": t.pass_score,
            "feedback": feedback, "test_type": "vibe_coding",
        }
    else:
        raise HTTPException(400, f"Unsupported test_type: {t.test_type}")


# ------------------- Vibe Coding helpers -------------------
@app.post("/test/{token}/run")
async def run_code(token: str, req: RunCodeRequest, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.token == token).first()
    if not t:
        raise HTTPException(404, "Test not found")
    if t.test_type != "vibe_coding":
        raise HTTPException(400, "Only vibe coding tests support code execution")
    problem_data = json.loads(t.questions_json)
    problem = problem_data.get("problem", "")
    examples = problem_data.get("examples", [])
    prompt = (
        f"You are a code execution simulator. Analyze the code logically.\n"
        f"Problem: {problem}\n"
        f"Examples: {json.dumps(examples, indent=2) if examples else 'No examples.'}\n"
        f"User code: {req.code}\n"
        'Return ONLY valid JSON: {"passed": true, "actual_output": "...", "expected_output": "...", "error": null}'
    )
    groq_res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=30,
    )
    groq_res.raise_for_status()
    raw = groq_res.json()["choices"][0]["message"]["content"]
    try:
        result = _extract_json_object(raw)
        result = _deep_clean(result)
        return result
    except Exception:
        raise HTTPException(500, "Invalid AI response")


@app.post("/test/{token}/chat")
async def chat_assistant(token: str, req: ChatRequest, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.token == token).first()
    if not t or t.test_type != "vibe_coding":
        raise HTTPException(404, "Vibe coding test not found")
    problem_data = json.loads(t.questions_json)
    system_prompt = (
        f"You are a helpful coding assistant. The candidate is solving:\n"
        f"Problem: {problem_data.get('problem', '')}\n"
        f"Current code: {req.context.get('code', '')}\n"
        "Answer ONLY the exact question asked. Be concise. Use code blocks when showing code."
    )
    groq_res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL,
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": req.message}],
              "temperature": 0.5, "max_tokens": 600},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=30,
    )
    groq_res.raise_for_status()
    return {"reply": groq_res.json()["choices"][0]["message"]["content"]}


@app.post("/test/{token}/edit")
async def edit_code(token: str, req: EditRequest, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.token == token).first()
    if not t:
        raise HTTPException(404, "Test not found")
    if t.test_type != "vibe_coding":
        raise HTTPException(400, "Edit only for vibe coding")
    problem_data = json.loads(t.questions_json)
    prompt = (
        f"Edit the code as instructed.\n"
        f"Problem: {problem_data.get('problem', '')}\n"
        f"Current code: {req.code}\n"
        f"Instruction: {req.instruction}\n"
        'Return ONLY valid JSON: {"new_code": "entire modified code", "explanation": "short explanation"}'
    )
    groq_res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=30,
    )
    groq_res.raise_for_status()
    raw = groq_res.json()["choices"][0]["message"]["content"]
    try:
        result = _extract_json_object(raw)
        result = _deep_clean(result)
        return result
    except Exception:
        raise HTTPException(500, "Invalid AI response")


@app.get("/vibe-codes/pending")
def get_pending_vibe_codes(db: Session = Depends(get_db)):
    tests = db.query(TestSession).filter(
        TestSession.test_type == "vibe_coding",
        TestSession.status == "pending_review"
    ).all()
    return [{
        "test_id": t.id, "token": t.token,
        "candidate_name": t.candidate_name, "candidate_email": t.candidate_email,
        "job_title": t.job_title, "submitted_at": str(t.submitted_at),
        "submission_data": json.loads(t.submission_data) if t.submission_data else {},
        "problem_data": json.loads(t.questions_json),
    } for t in tests]


@app.post("/vibe-codes/{test_id}/review")
def review_vibe_code(test_id: int, req: ReviewRequest, db: Session = Depends(get_db)):
    t = db.query(TestSession).filter(TestSession.id == test_id).first()
    if not t or t.test_type != "vibe_coding":
        raise HTTPException(404, "Vibe coding test not found")
    if t.status != "pending_review":
        raise HTTPException(400, "Test already reviewed")

    t.passed = req.passed
    t.status = "reviewed"
    t.answers_json = json.dumps({"feedback": req.feedback, "reviewed_at": str(datetime.now(timezone.utc))})
    db.commit()

    if req.passed:
        current_idx = _get_round_index(t.application_id, t.job_id)
        try:
            trigger_next_round(application_id=t.application_id, current_round_index=current_idx)
        except Exception as e:
            print(f"[Test] Vibe review router error: {e}")
    else:
        try:
            requests.patch(
                f"{ORCHESTRATOR_URL}/applications/{t.application_id}/status",
                json={"status": "rejected"}, timeout=5,
            )
        except:
            pass

    return {"status": "reviewed", "passed": req.passed}
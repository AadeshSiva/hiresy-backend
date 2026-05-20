"""
Central round router for the Hiersy hiring pipeline.

Usage (from any service's submit endpoint):
    from agent_common.round_router import trigger_next_round
    trigger_next_round(application_id=t.application_id, current_round_index=0)

    - current_round_index is the 0-based index of the round that just passed.
    - Pass -1 after evaluation (Round 0) to trigger the very first HR-configured round.
"""

import requests
import logging
from agent_common.config import (
    ORCHESTRATOR_URL,
    TEST_SERVICE_URL,
    CODING_SERVICE_URL,
    COMM_SERVICE_URL,
    LIVEHR_SERVICE_URL,
    FRONTEND_URL,
)
from agent_common.email_utils import (
    send_email,
    build_shortlist_with_test_email,
    build_communication_invite_email,
    build_coding_invite_email,
    build_hr_invite_email,
)

log = logging.getLogger(__name__)

# ── Round name → category ─────────────────────────────────────────────────────
SHORTLISTING_ROUNDS = {"MCQ Test", "Vibe Coding", "Aptitude / Logical"}
COMM_VERBAL_ROUNDS  = {"Verbal Ability Test"}
COMM_SPOKEN_ROUNDS  = {"Spoken English Test"}
CODING_ROUNDS       = {"Basic Programming"}
LIVEHR_ROUNDS       = {"Technical HR", "Salary & Policy Discussion"}

# ── test_type mapping for agent_test ─────────────────────────────────────────
TEST_TYPE_MAP = {
    "MCQ Test":           "mcq",
    "Aptitude / Logical": "aptitude",
    "Vibe Coding":        "vibe_coding",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_candidate(application_id: int) -> dict:
    res = requests.get(f"{ORCHESTRATOR_URL}/application/{application_id}", timeout=8)
    res.raise_for_status()
    return res.json()

def _get_job(job_id: int) -> dict:
    res = requests.get(f"{ORCHESTRATOR_URL}/jobs/{job_id}", timeout=8)
    res.raise_for_status()
    return res.json()

def _get_hr_name(job: dict) -> str:
    try:
        posted_by = job.get("posted_by", "")
        if not posted_by:
            return "Hiersy Team"
        res = requests.get(f"{ORCHESTRATOR_URL}/users/by-email/{posted_by}", timeout=5)
        if res.ok:
            return res.json().get("name", "Hiersy Team")
    except Exception as e:
        log.warning(f"HR name fetch failed: {e}")
    return "Hiersy Team"

def _get_rounds(job: dict) -> list[str]:
    raw = job.get("rounds") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]

def _update_status(application_id: int, status: str):
    try:
        requests.patch(
            f"{ORCHESTRATOR_URL}/applications/{application_id}/status",
            json={"status": status},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"Status update failed for app {application_id}: {e}")

# ── Main entry point ──────────────────────────────────────────────────────────
def trigger_next_round(application_id: int, current_round_index: int) -> dict:
    """
    Determine and trigger the next round after `current_round_index` passes.

    current_round_index:
        -1  → called after Round 0 (AI evaluation); trigger rounds[0]
         0  → rounds[0] passed; trigger rounds[1]
         n  → rounds[n] passed; trigger rounds[n+1] or mark selected
    """
    try:
        cand = _get_candidate(application_id)
    except Exception as e:
        log.error(f"[Router] Could not fetch candidate {application_id}: {e}")
        return {"error": str(e)}

    job_id = cand.get("job_id")
    name   = cand.get("full_name", "Candidate")
    email  = cand.get("email", "")
    skills = cand.get("technical_skills", "General")

    if not email:
        log.warning(f"[Router] No email for app {application_id}, skipping.")
        return {"error": "no email"}

    try:
        job = _get_job(job_id)
    except Exception as e:
        log.error(f"[Router] Could not fetch job {job_id}: {e}")
        return {"error": str(e)}

    job_title  = job.get("job_name", "the position")
    hr_name    = _get_hr_name(job)
    posted_by  = job.get("posted_by", "")
    rounds     = _get_rounds(job)
    next_index = current_round_index + 1

    # No more rounds → pipeline complete (selected email triggered by HR manually)
    if next_index >= len(rounds):
        log.info(f"[Router] App {application_id} completed all rounds. Awaiting HR decision.")
        _update_status(application_id, "selected")
        return {"status": "all_rounds_complete"}

    next_round = rounds[next_index]
    new_status = f"round_{next_index + 1}"
    _update_status(application_id, new_status)

    log.info(f"[Router] App {application_id} → triggering round {next_index + 1}: '{next_round}'")

    # ── Shortlisting Test ─────────────────────────────────────────────────────
    if next_round in SHORTLISTING_ROUNDS:
        test_type = TEST_TYPE_MAP.get(next_round, "mcq")
        try:
            res = requests.post(
                f"{TEST_SERVICE_URL}/tests/create",
                json={
                    "application_id": application_id,
                    "job_id": job_id,
                    "candidate_name": name,
                    "candidate_email": email,
                    "job_title": job_title,
                    "job_skills": skills,
                    "test_type": test_type,
                    "duration_mins": 20,
                    "total_questions": 10,
                    "pass_score": 60,
                },
                timeout=60,
            )
            if res.ok:
                token = res.json().get("token")
                test_link = f"{FRONTEND_URL}/test/{token}"
                html = build_shortlist_with_test_email(name, job_title, test_link, hr_name)
                send_email(email, f"Online Assessment — {job_title}", html)
                log.info(f"[Router] Shortlisting test created & email sent. Token: {token}")
                return {"round": next_round, "token": token, "email_sent": True}
            else:
                log.error(f"[Router] Test creation failed: {res.status_code} {res.text[:200]}")
                return {"error": "test_creation_failed"}
        except Exception as e:
            log.error(f"[Router] Shortlisting round error: {e}")
            return {"error": str(e)}

    # ── Verbal Ability Test ───────────────────────────────────────────────────
    if next_round in COMM_VERBAL_ROUNDS:
        try:
            res = requests.post(
                f"{COMM_SERVICE_URL}/comm/create",
                json={
                    "application_id": application_id,
                    "job_id": job_id,
                    "candidate_name": name,
                    "candidate_email": email,
                    "job_title": job_title,
                    "test_type": "verbal",
                    "duration_mins": 10,
                    "total_questions": 5,
                    "pass_score": 60,
                },
                timeout=30,
            )
            if res.ok:
                token = res.json().get("token")
                test_link = f"{FRONTEND_URL}/verbal-test/{token}"
                html = build_communication_invite_email(name, job_title, test_link, hr_name)
                send_email(email, f"Communication Assessment — {job_title}", html)
                log.info(f"[Router] Verbal test created & email sent. Token: {token}")
                return {"round": next_round, "token": token, "email_sent": True}
            else:
                log.error(f"[Router] Verbal test creation failed: {res.status_code} {res.text[:200]}")
                return {"error": "verbal_creation_failed"}
        except Exception as e:
            log.error(f"[Router] Verbal round error: {e}")
            return {"error": str(e)}

    # ── Spoken English Test ───────────────────────────────────────────────────
    if next_round in COMM_SPOKEN_ROUNDS:
        try:
            res = requests.post(
                f"{COMM_SERVICE_URL}/spoken/create",
                json={
                    "application_id": application_id,
                    "job_id": job_id,
                    "candidate_name": name,
                    "candidate_email": email,
                    "job_title": job_title,
                },
                timeout=30,
            )
            if res.ok:
                token = res.json().get("token")
                test_link = f"{FRONTEND_URL}/spoken-test/{token}"
                html = build_communication_invite_email(name, job_title, test_link, hr_name)
                send_email(email, f"Communication Assessment — {job_title}", html)
                log.info(f"[Router] Spoken test created & email sent. Token: {token}")
                return {"round": next_round, "token": token, "email_sent": True}
            else:
                log.error(f"[Router] Spoken test creation failed: {res.status_code} {res.text[:200]}")
                return {"error": "spoken_creation_failed"}
        except Exception as e:
            log.error(f"[Router] Spoken round error: {e}")
            return {"error": str(e)}

    # ── Coding Round ──────────────────────────────────────────────────────────
    if next_round in CODING_ROUNDS:
        try:
            res = requests.post(
                f"{CODING_SERVICE_URL}/coding/create",
                json={
                    "application_id": application_id,
                    "job_id": job_id,
                    "candidate_name": name,
                    "candidate_email": email,
                    "job_title": job_title,
                    "job_skills": skills,
                    "duration_mins": 60,
                },
                timeout=60,
            )
            if res.ok:
                token = res.json().get("token")
                html = build_coding_invite_email(name, job_title, token, 60, hr_name)
                send_email(email, f"Coding Assessment — {job_title}", html)
                log.info(f"[Router] Coding round created & email sent. Token: {token}")
                return {"round": next_round, "token": token, "email_sent": True}
            else:
                log.error(f"[Router] Coding creation failed: {res.status_code} {res.text[:200]}")
                return {"error": "coding_creation_failed"}
        except Exception as e:
            log.error(f"[Router] Coding round error: {e}")
            return {"error": str(e)}

    # ── Live HR ───────────────────────────────────────────────────────────────
    if next_round in LIVEHR_ROUNDS:
        try:
            res = requests.post(
                f"{LIVEHR_SERVICE_URL}/livehr/create",
                json={
                    "application_id": application_id,
                    "job_id": job_id,
                    "candidate_name": name,
                    "candidate_email": email,
                    "job_title": job_title,
                    "job_skills": skills,
                    "hr_email": posted_by,
                },
                timeout=30,
            )
            if res.ok:
                data           = res.json()
                token          = data.get("token")
                meet_url       = data.get("meet_url")
                scheduled_time = data.get("scheduled_time", "")

                # ── Email candidate ───────────────────────────────────────────
                candidate_html = build_hr_invite_email(
                    name, job_title, meet_url, scheduled_time, hr_name
                )
                send_email(
                    email,
                    f"HR Interview Invitation — {job_title}",
                    candidate_html,
                )
                log.info(f"[Router] Candidate interview email sent to {email}")

                # ── Email HR ──────────────────────────────────────────────────
                if posted_by:
                    hr_html = build_hr_invite_email(
                        name, job_title, meet_url, scheduled_time, hr_name
                    )
                    send_email(
                        posted_by,
                        f"Interview Scheduled — {job_title} | {name}",
                        hr_html,
                    )
                    log.info(f"[Router] HR interview email sent to {posted_by}")

                log.info(f"[Router] Live HR session created. Token: {token}")
                return {"round": next_round, "token": token, "email_sent": True}
            else:
                log.error(f"[Router] LiveHR create failed: {res.status_code} {res.text[:200]}")
                return {"error": "livehr_creation_failed"}
        except Exception as e:
            log.error(f"[Router] LiveHR round error: {e}")
            return {"error": str(e)}

    log.warning(f"[Router] Unknown round type: '{next_round}' — skipping.")
    return {"error": f"unknown_round: {next_round}"}
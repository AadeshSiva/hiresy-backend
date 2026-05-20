import sqlite3
import requests
import os
from agent_common.config import FRONTEND_URL

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_automation.db")

def format_post(job: dict, job_id: str = "") -> str:
    types = {"ft": "Full-Time", "pt": "Part-Time", "ct": "Contract"}
    depts = {"dev": "Development", "sal": "Sales", "mkt": "Marketing"}

    title = job.get("title", "")
    skills = job.get("skills", "").replace(",", " · ")
    loc = job.get("location", "")
    sal = job.get("salary", "")
    desc = job.get("description", "")
    jtype = types.get(job.get("job_type", ""), job.get("job_type", ""))
    dept = depts.get(job.get("department", ""), job.get("department", ""))
    opens = job.get("openings", "")
    dead = job.get("deadline", "")

    text = f"🚀 We're Hiring — {title}!\n\n{desc}\n\n"
    text += f"📌 Role: {title}\n"
    text += f"🏢 Department: {dept}\n"
    text += f"💼 Type: {jtype}\n"
    if loc:
        text += f"📍 Work Style: {loc}\n"
    if sal:
        text += f"💰 Salary: {sal}\n"
    if opens:
        text += f"🔢 Openings: {opens}\n"
    if dead:
        text += f"📅 Deadline: {dead}\n"
    if skills:
        text += f"\n🛠 Skills: {skills}\n"
    text += "\n👉 Apply now or DM us!\n"
    if job_id:
        text += f"🔗 Job details & application: {FRONTEND_URL}/job/{job_id}\n\n"
    else:
        text += "\n"
    text += "#Hiring #JobOpening #NowHiring #" + dept.replace(' ', '') + "\n"
    text += "⚠️ Notice: This is a test job posting created for system testing. Please do not submit applications.\n"
    return text

def get_account(hr_email: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM linkedin_accounts WHERE hr_email = ?", (hr_email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def post_to_linkedin(access_token: str, person_urn: str, content: str) -> dict:
    res = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content},
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            }
        }
    )
    try:
        body = res.json()
    except:
        body = {"raw": res.text}
    print(f"[LinkedIn] Post status={res.status_code} body={body}")
    return {"status_code": res.status_code, "body": body}

def generate_and_post(job_id: str, job_details: dict) -> dict:
    post_text = format_post(job_details, job_id)
    hr_email = job_details.get("posted_by")
    account = get_account(hr_email)

    if not account:
        return {"success": False, "error": "LinkedIn not connected.", "generated_post": post_text}

    person_urn = (account.get("person_urn") or "").strip()
    access_token = (account.get("access_token") or "").strip()

    if not person_urn:
        return {"success": False, "error": "LinkedIn reconnect needed (missing person_urn).", "generated_post": post_text}

    result = post_to_linkedin(access_token, person_urn, post_text)
    success = result["status_code"] in (200, 201)

    return {
        "success": success,
        "generated_post": post_text,
        "linkedin_status": result["status_code"],
        "linkedin_body": result["body"]
    }
#agent_evaluator/main.py
import os
import re
import json
import requests
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent_common.config import (
    GROQ_API_KEY, GROQ_URL, GROQ_MODEL, GITHUB_TOKEN,
    ORCHESTRATOR_URL, SHORTLIST_MIN_SCORE,
)
from agent_common.round_router import trigger_next_round
from agent_common.database import init_db

app = FastAPI(title="Hiersy Evaluator Agent")

@app.on_event("startup")
def startup_event():
    init_db()

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["http://localhost:5173", "https://hiresyai.vercel.app"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# ------------------- GitHub fetch -------------------
def fetch_github_raw(github_url: str):
    if not github_url:
        return {}, "No GitHub provided."
    match = re.search(r'github\.com/([a-zA-Z0-9\-]+)', github_url)
    if not match:
        return {}, "Could not parse GitHub username."
    username = match.group(1)
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    try:
        profile = requests.get(f"https://api.github.com/users/{username}", headers=headers, timeout=8).json()
        repos_res = requests.get(f"https://api.github.com/users/{username}/repos?sort=pushed&per_page=100", headers=headers, timeout=10)
        repos = repos_res.json() if repos_res.ok and isinstance(repos_res.json(), list) else []
        lang_counts, total_stars, total_forks, total_size = {}, 0, 0, 0
        repo_types = {"original": 0, "forked": 0}
        for r in repos:
            lang = r.get("language") or "Other"
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
            total_stars += r.get("stargazers_count", 0)
            total_forks += r.get("forks_count", 0)
            total_size += r.get("size", 0)
            if r.get("fork"):
                repo_types["forked"] += 1
            else:
                repo_types["original"] += 1
        top_repos = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:8]
        top_langs = sorted(lang_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        commit_by_month = {}
        try:
            events_res = requests.get(f"https://api.github.com/users/{username}/events?per_page=100", headers=headers, timeout=8)
            if events_res.ok:
                from datetime import datetime, timezone
                for ev in events_res.json():
                    if ev.get("type") == "PushEvent":
                        dt = datetime.fromisoformat(ev["created_at"].replace("Z", "+00:00"))
                        mk = dt.strftime("%b")
                        commit_by_month[mk] = commit_by_month.get(mk, 0) + ev.get("payload", {}).get("size", 1)
        except:
            pass
        months_order = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        commit_activity = [{"month": m, "commits": commit_by_month.get(m, 0)} for m in months_order]
        orig = repo_types["original"]
        if total_stars > 50 or orig > 15:
            hint = "Strong GitHub (75-90)"
        elif total_stars > 10 or orig > 5:
            hint = "Decent GitHub (55-74)"
        elif len(repos) > 0:
            hint = "Weak GitHub (30-54)"
        else:
            hint = "Empty GitHub (10-29)"
        raw = {
            "username": username, "name": profile.get("name", username), "bio": profile.get("bio", ""),
            "followers": profile.get("followers", 0), "following": profile.get("following", 0),
            "total_repos": len(repos), "public_repos": profile.get("public_repos", len(repos)),
            "languages": lang_counts, "total_stars": total_stars, "total_forks": total_forks,
            "total_size_mb": round(total_size / 1024, 1), "repo_types": repo_types,
            "commit_activity": commit_activity,
            "top_repos": [{"name": r["name"], "stars": r.get("stargazers_count", 0), "forks": r.get("forks_count", 0),
                           "language": r.get("language", "?"), "description": (r.get("description") or "")[:70],
                           "size_kb": r.get("size", 0), "fork": r.get("fork", False)} for r in top_repos],
        }
        text = (f"Username: {username} | Repos: {len(repos)} | Stars: {total_stars} | "
                f"Forks: {total_forks} | Followers: {profile.get('followers', 0)} | "
                f"Original: {orig} | Forked: {repo_types['forked']}\n"
                f"Top languages: {', '.join(f'{l}({c})' for l, c in top_langs)}\n"
                f"Top repos: " + " | ".join(f"{r['name']}(⭐{r.get('stargazers_count', 0)})" for r in top_repos[:5]) +
                f"\nScoring hint: {hint}")
        return raw, text
    except Exception as e:
        return {}, f"GitHub fetch failed: {e}"

# ------------------- LeetCode fetch -------------------
LEETCODE_URL = "https://leetcode.com/graphql"
LC_HEADERS = {"Content-Type": "application/json", "Referer": "https://leetcode.com", "User-Agent": "Mozilla/5.0"}
PROFILE_QUERY = """query getUserProfile($username: String!) { matchedUser(username: $username) {
    username profile { ranking reputation }
    submitStatsGlobal { acSubmissionNum { difficulty count } }
    tagProblemCounts { advanced { tagName problemsSolved } intermediate { tagName problemsSolved } fundamental { tagName problemsSolved } }
    userCalendar { totalActiveDays } } }"""
CONTEST_QUERY = """query userContestRankingInfo($username: String!) {
    userContestRanking(username: $username) { rating attendedContestsCount } }"""

async def fetch_leetcode_raw(leetcode_url: str):
    if not leetcode_url:
        return {}, "No LeetCode provided."
    match = re.search(r'leetcode\.com/(?:u/)?([a-zA-Z0-9_\-]+)', leetcode_url)
    if not match:
        return {}, "Could not parse LeetCode username."
    username = match.group(1)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r1 = await client.post(LEETCODE_URL, json={"query": PROFILE_QUERY, "variables": {"username": username}}, headers=LC_HEADERS)
            r1.raise_for_status()
            user = (r1.json().get("data") or {}).get("matchedUser") or {}
            if not user:
                return {}, f"LeetCode user '{username}' not found."
            r2 = await client.post(LEETCODE_URL, json={"query": CONTEST_QUERY, "variables": {"username": username}}, headers=LC_HEADERS)
            r2.raise_for_status()
            contest = (r2.json().get("data") or {}).get("userContestRanking") or {}
        stats = user.get("submitStatsGlobal", {}).get("acSubmissionNum", [])
        solved = {s["difficulty"]: s["count"] for s in stats}
        tags = user.get("tagProblemCounts", {})
        all_tags = []
        for section in ["fundamental", "intermediate", "advanced"]:
            all_tags += tags.get(section, [])
        top_tags = sorted(all_tags, key=lambda x: x.get("problemsSolved", 0), reverse=True)[:6]
        total = sum(solved.values())
        hard = solved.get("Hard", 0)
        if total > 300 or hard > 50:
            hint = "Strong LeetCoder (75-90)"
        elif total > 100 or hard > 10:
            hint = "Decent LeetCoder (55-74)"
        elif total > 0:
            hint = "Beginner LeetCoder (30-54)"
        else:
            hint = "No problems solved (0-20)"
        raw = {
            "username": username, "easy": solved.get("Easy", 0), "medium": solved.get("Medium", 0),
            "hard": hard, "total": total, "ranking": user.get("profile", {}).get("ranking", "N/A"),
            "reputation": user.get("profile", {}).get("reputation", 0),
            "active_days": user.get("userCalendar", {}).get("totalActiveDays", 0),
            "contest_rating": contest.get("rating", 0), "contests_attended": contest.get("attendedContestsCount", 0),
            "top_tags": top_tags,
        }
        text = (f"Username: {username} | Total: {total} (Easy:{raw['easy']} Medium:{raw['medium']} Hard:{hard}) | "
                f"Rank: {raw['ranking']} | Active days: {raw['active_days']} | "
                f"Contest rating: {raw.get('contest_rating', 'N/A')} ({raw.get('contests_attended', 0)} contests)\n"
                f"Top topics: {', '.join(t['tagName'] for t in top_tags[:5])}\nScoring hint: {hint}")
        return raw, text
    except Exception as e:
        return {}, f"LeetCode fetch failed: {e}"

# ------------------- Groq call -------------------
def call_groq(prompt: str) -> dict:
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY not configured")
    res = requests.post(
        GROQ_URL,
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 1500},
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=40
    )
    if not res.ok:
        raise Exception(f"Groq error {res.status_code}: {res.text[:200]}")
    raw = res.json()["choices"][0]["message"]["content"]
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        s = clean.find("{"); e = clean.rfind("}")
        return json.loads(clean[s:e+1])
    except json.JSONDecodeError:
        def get_score(label):
            m = re.search(rf'"{label}"\s*:\s*{{[^}}]*"score"\s*:\s*(\d+)', clean)
            if not m:
                m = re.search(rf'"score"\s*:\s*(\d+)[^}}]*"{label}"', clean)
            return int(m.group(1)) if m else 0
        def get_str(k):
            m = re.search(rf'"{k}"\s*:\s*"([^"]+)"', clean)
            return m.group(1) if m else ""
        r = get_score("resume"); g = get_score("github"); lc = get_score("leetcode")
        return {
            "final_score": round(r * 0.45 + g * 0.30 + lc * 0.25),
            "hiring_recommendation": get_str("hiring_recommendation") or "Borderline",
            "summary": get_str("summary"),
            "component_scores": {
                "resume":   {"score": r, "reasoning": get_str("reasoning") or ""},
                "github":   {"score": g, "reasoning": ""},
                "leetcode": {"score": lc, "reasoning": ""},
                "linkedin": {"score": 0, "reasoning": ""},
            },
            "inconsistencies": []
        }

# ------------------- Evaluation endpoint -------------------
class EvalRequest(BaseModel):
    application_id: int
    resume_text: str
    job_description: str
    github_url: str = ""
    linkedin_url: str = ""
    leetcode_url: str = ""

@app.post("/eval/evaluate")
async def evaluate(req: EvalRequest):
    try:
        github_raw, github_text = fetch_github_raw(req.github_url)
        leetcode_raw, leetcode_text = await fetch_leetcode_raw(req.leetcode_url)

        linkedin_status = "URL provided." if req.linkedin_url else "NOT PROVIDED."
        resume_hint = "Score how well the resume matches the JD skills and experience. Range 0-100."

        prompt = f"""You are a strict senior technical recruiter. Evaluate this candidate OBJECTIVELY.
Use the REAL data provided. Do NOT default all scores to the same value.

JOB DESCRIPTION:
{req.job_description[:600]}

CANDIDATE RESUME:
{req.resume_text[:1500]}

GITHUB (REAL DATA):
{github_text if github_raw else "NOT PROVIDED — score must be 0."}

LEETCODE (REAL DATA):
{leetcode_text if leetcode_raw else "NOT PROVIDED — score must be 0."}

LINKEDIN: {linkedin_status}

SCORING RULES:
- Resume score: {resume_hint}
- GitHub score: Use the scoring hint from the GitHub data above. 0 if not provided.
- LeetCode score: Use the scoring hint from LeetCode data above. 0 if not provided.
- LinkedIn: NOT included in final score. Set to 0 if missing, 50 if URL provided.
- Final score = (resume x 0.45) + (github x 0.30) + (leetcode x 0.25). Compute this exactly.
- hiring_recommendation: Strong Hire if >=80, Hire if >=65, Borderline if >=50, No Hire if <50.
- Each score must be DIFFERENT unless genuinely equal.

Return ONLY this JSON (no markdown, no extra text):
{{
  "final_score": <integer>,
  "hiring_recommendation": "<Strong Hire|Hire|Borderline|No Hire>",
  "summary": "<2 sentences citing specific data points>",
  "component_scores": {{
    "resume":   {{"score": <0-100>, "reasoning": "<cite specific skills match>"}},
    "github":   {{"score": <0-100>, "reasoning": "<cite actual stats>"}},
    "leetcode": {{"score": <0-100>, "reasoning": "<cite actual counts>"}},
    "linkedin": {{"score": <0-100>, "reasoning": "<one sentence>"}}
  }},
  "inconsistencies": []
}}"""

        result = call_groq(prompt)

        cs = result.setdefault("component_scores", {})
        if not github_raw:
            cs.setdefault("github", {})["score"] = 0
            cs["github"]["reasoning"] = "No GitHub URL provided."
        if not leetcode_raw or leetcode_raw.get("total", 0) == 0:
            cs.setdefault("leetcode", {})["score"] = 0
            cs["leetcode"]["reasoning"] = "No LeetCode URL provided or no problems solved."
        if not req.linkedin_url:
            cs.setdefault("linkedin", {})["score"] = 0
            cs["linkedin"]["reasoning"] = "No LinkedIn URL provided."

        r  = cs.get("resume",   {}).get("score", 0)
        g  = cs.get("github",   {}).get("score", 0)
        lc = cs.get("leetcode", {}).get("score", 0)
        result["final_score"] = round(r * 0.45 + g * 0.30 + lc * 0.25)

        fs = result["final_score"]
        result["hiring_recommendation"] = (
            "Strong Hire" if fs >= 80 else
            "Hire"        if fs >= 65 else
            "Borderline"  if fs >= 50 else
            "No Hire"
        )
        result["application_id"] = req.application_id
        result["github_raw"]     = github_raw
        result["leetcode_raw"]   = leetcode_raw
        result["eval_summary"]   = result.get("summary", "")

        # ── Round 0 complete → trigger Round 1 via router ────────────────────
        if fs > SHORTLIST_MIN_SCORE and req.application_id:
            try:
                trigger_next_round(
                    application_id=req.application_id,
                    current_round_index=-1,   # -1 = after eval, trigger rounds[0]
                )
            except Exception as e:
                print(f"[Eval] Round router failed for app {req.application_id}: {e}")

        return result

    except Exception as e:
        print(f"EVAL ERROR: {e}")
        raise HTTPException(500, str(e))

@app.get("/health")
def health():
    return {"status": "ok", "groq_configured": bool(GROQ_API_KEY)}
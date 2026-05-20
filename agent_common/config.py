import os
from dotenv import load_dotenv

# Load .env from the parent directory (backend/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH, override=True)

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:aadesh@localhost:5432/herisy_new")

# Groq AI
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "") or os.getenv("GitHub_API", "")

# LinkedIn Scraping (optional, for evaluator)
LI_EMAIL = os.getenv("LI_EMAIL", "")
LI_PASSWORD = os.getenv("LI_PASSWORD", "")

# SMTP Email
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# Frontend & HR
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
HR_EMAIL = os.getenv("HR_EMAIL", "hr@company.com")

# Service URLs (internal communication)
# Base Host
HOST = os.getenv("HOST", "127.0.0.1")

# Ports
ORCHESTRATOR_PORT = os.getenv("ORCHESTRATOR_PORT", "8000")
EVALUATOR_PORT = os.getenv("EVALUATOR_PORT", "8001")
TEST_SERVICE_PORT = os.getenv("TEST_SERVICE_PORT", "8002")
COMM_SERVICE_PORT = os.getenv("COMM_SERVICE_PORT", "8003")
CODING_SERVICE_PORT = os.getenv("CODING_SERVICE_PORT", "8004")
LIVEHR_SERVICE_PORT = os.getenv("LIVEHR_SERVICE_PORT", "8005")
BGV_SERVICE_PORT = os.getenv("BGV_SERVICE_PORT", "8006")
OFFER_SERVICE_PORT = os.getenv("OFFER_SERVICE_PORT", "8007")

# Service URLs
ORCHESTRATOR_URL = f"http://{HOST}:{ORCHESTRATOR_PORT}"
EVALUATOR_URL = f"http://{HOST}:{EVALUATOR_PORT}"
TEST_SERVICE_URL = f"http://{HOST}:{TEST_SERVICE_PORT}"
COMM_SERVICE_URL = f"http://{HOST}:{COMM_SERVICE_PORT}"
CODING_SERVICE_URL = f"http://{HOST}:{CODING_SERVICE_PORT}"
LIVEHR_SERVICE_URL = f"http://{HOST}:{LIVEHR_SERVICE_PORT}"
BGV_SERVICE_URL = f"http://{HOST}:{BGV_SERVICE_PORT}"
OFFER_SERVICE_URL = f"http://{HOST}:{OFFER_SERVICE_PORT}"

SERVICES = {
    "orchestrator": ORCHESTRATOR_URL,
    "evaluator": EVALUATOR_URL,
    "test_service": TEST_SERVICE_URL,
    "comm_service": COMM_SERVICE_URL,
    "coding_service": CODING_SERVICE_URL,
    "livehr_service": LIVEHR_SERVICE_URL,
    "bgv_service": BGV_SERVICE_URL,
    "offer_service": OFFER_SERVICE_URL,
}

# Scoring & Pipeline 
SHORTLIST_MIN_SCORE = int(os.getenv("SHORTLIST_MIN_SCORE", "40"))

# LinkedIn OAuth (used in orchestrator)
LI_CLIENT_ID = os.getenv("LI_CLIENT_ID", "")
LI_CLIENT_SECRET = os.getenv("LI_CLIENT_SECRET", "")
LI_REDIRECT_URI = os.getenv("LI_REDIRECT_URI", "http://localhost:8000/linkedin/callback")
LI_SCOPE = os.getenv("LI_SCOPE", "openid profile w_member_social")

# Helper functions
def is_smtp_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASS)

def is_groq_configured() -> bool:
    return bool(GROQ_API_KEY)
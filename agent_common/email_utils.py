#agent_common/email_utils.py
import os
from .config import FRONTEND_URL

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SMTP_FROM = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", ""))

def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not SENDGRID_API_KEY:
        print(f"SendGrid not configured – would send to {to_email}: {subject}")
        return False
    try:
        import urllib.request, json
        data = json.dumps({
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": SMTP_FROM, "name": "Hiersy"},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}]
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=data,
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req) as res:
            print(f"Email sent to {to_email}: {subject} (status {res.status})")
        return True
    except Exception as e:
        print(f"Email failed to {to_email}: {e}")
        return False

def wrap_email_body(body_content: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 20px;">
  <tr><td align="center">
  <table width="560" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.07);">
    <tr><td style="padding:28px 40px 20px;text-align:center;border-bottom:1px solid #f0f0f0;">
      <p style="margin:0;font-size:20px;font-weight:700;color:#FF4400;">Hiersy</p>
    </td></tr>
    <tr><td style="padding:36px 40px;">{body_content}</td></tr>
    <tr><td style="padding:16px 40px;background:#fafafa;border-top:1px solid #f0f0f0;">
      <p style="margin:0;font-size:11px;color:#ccc;text-align:center;">Hiersy AI Hiring Platform</p>
    </td></tr>
  </table>
  </td></tr>
</table>
</body></html>"""

def _btn(label: str, url: str) -> str:
    return (
        f'<p style="text-align:center;margin:28px 0;">'
        f'<a href="{url}" style="display:inline-block;background:#FF4400;color:#ffffff;'
        f'text-decoration:none;padding:13px 32px;border-radius:8px;font-weight:600;'
        f'font-size:15px;letter-spacing:0.3px;">{label}</a></p>'
    )

def _signature(hr_name: str) -> str:
    return (
        f'<p style="margin:28px 0 0;font-size:14px;color:#333;line-height:1.7;">'
        f'Best Regards,<br>'
        f'<strong>{hr_name}</strong><br>'
        f'Talent Acquisition Team<br>'
        f'Hiersy</p>'
    )

def _body_style() -> str:
    return 'style="font-size:15px;color:#333;line-height:1.8;margin:0 0 14px;"'

def build_shortlist_email(candidate_name, job_title, hr_name="Hiersy Team", has_test=True):
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>We are pleased to inform you that your profile has been shortlisted for the position of <strong>{job_title}</strong> at Hiersy.</p>
    <p {bs}>Our recruitment team will shortly connect with you regarding the next steps in the selection process.</p>
    <p {bs}>Congratulations once again on being shortlisted. We look forward to interacting with you further.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_shortlist_with_test_email(candidate_name, job_title, test_link, hr_name="Hiersy Team"):
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>Further to your shortlisting for <strong>{job_title}</strong>, we invite you to complete the <strong>Online Assessment Test</strong>.</p>
    <p {bs}>Please complete the test within <strong>1 week</strong>.</p>
    {_btn("Take Test", test_link)}
    <p {bs}>We wish you the very best for your assessment.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_test_invite_email(candidate_name, job_title, token, duration, total_questions, hr_name="Hiersy Team"):
    test_url = f"{FRONTEND_URL}/test/{token}"
    return build_shortlist_with_test_email(candidate_name, job_title, test_url, hr_name)

def build_communication_invite_email(candidate_name, job_title, test_link, hr_name="Hiersy Team"):
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>We invite you to participate in the <strong>Communication Assessment Round</strong> for <strong>{job_title}</strong>.</p>
    <p {bs}>Please complete the assessment within <strong>1 week</strong>.</p>
    {_btn("Take Assessment", test_link)}
    <p {bs}>We wish you the very best.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_coding_invite_email(candidate_name, job_title, token, duration, hr_name="Hiersy Team"):
    coding_url = f"{FRONTEND_URL}/coding/{token}"
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>Congratulations on clearing the previous stage for <strong>{job_title}</strong>!</p>
    <p {bs}>You are invited to participate in the <strong>Coding Assessment Round</strong>. Please complete within <strong>1 week</strong>.</p>
    {_btn("Take Coding Test", coding_url)}
    <p {bs}>We wish you success in the upcoming round.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_hr_invite_email(candidate_name, job_title, meet_url, scheduled_time="", hr_name="Hiersy Team"):
    if scheduled_time:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(scheduled_time)
            meeting_date = dt.strftime("%A, %d %B %Y")
            meeting_time = dt.strftime("%I:%M %p")
        except Exception:
            meeting_date = scheduled_time
            meeting_time = ""
    else:
        meeting_date = "To be communicated"
        meeting_time = "To be communicated"
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>You are invited for an <strong>HR Discussion</strong> for <strong>{job_title}</strong>.</p>
    <table cellpadding="0" cellspacing="0" style="margin:16px 0;border-left:3px solid #FF4400;padding-left:16px;">
      <tr><td style="font-size:14px;color:#555;padding:4px 0;"><strong>Date:</strong>&nbsp; {meeting_date}</td></tr>
      <tr><td style="font-size:14px;color:#555;padding:4px 0;"><strong>Time:</strong>&nbsp; {meeting_time}</td></tr>
    </table>
    {_btn("Join Meeting", meet_url)}
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_selected_email(candidate_name, job_title, bg_verification_link="", hr_name="Hiersy Team"):
    bs = _body_style()
    bg_section = ""
    if bg_verification_link:
        bg_section = f"""
        <p {bs}>Please complete the <strong>Background Verification</strong> within <strong>1 week</strong>.</p>
        {_btn("Start Verification", bg_verification_link)}
        """
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>We are delighted to inform you that you have been selected for <strong>{job_title}</strong> at Hiersy. Congratulations!</p>
    {bg_section}
    <p {bs}>We look forward to welcoming you to Hiersy.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_offer_letter_email(candidate_name, job_title, offer_link, esign_link, hr_name="Hiersy Team"):
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>We are pleased to extend an offer for <strong>{job_title}</strong> at Hiersy.</p>
    {_btn("View Offer Letter", offer_link)}
    <p {bs}>Please e-sign to confirm your acceptance:</p>
    {_btn("E-Sign Offer Letter", esign_link)}
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_rejection_email(candidate_name, job_title, hr_name="Hiersy Team"):
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>Thank you for your time during the recruitment process for <strong>{job_title}</strong>.</p>
    <p {bs}>After careful consideration, we have decided to move forward with other candidates. We wish you the very best in your career journey.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def build_outcome_email(candidate_name, job_title, passed, hr_name="Hiersy Team"):
    if passed:
        return build_selected_email(candidate_name, job_title, hr_name=hr_name)
    return build_rejection_email(candidate_name, job_title, hr_name=hr_name)

def build_bgv_invite_email(candidate_name, candidate_email, token, hr_name="Hiersy Team"):
    bgv_link = f"{FRONTEND_URL}/back/{token}"
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>Congratulations on clearing all interview rounds! Please complete the <strong>Background Verification (BGV)</strong> as the final step.</p>
    <p {bs}>Documents required: Identity Proof, Education Documents, Experience Letters, Criminal affidavit.</p>
    {_btn("Start Background Verification", bgv_link)}
    <p {bs}>Please submit within <strong>5 days</strong>.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def send_bgv_invite_email(candidate_name, candidate_email, token, hr_name="Hiersy Team"):
    html_body = build_bgv_invite_email(candidate_name, candidate_email, token, hr_name)
    subject = f"Background Verification Required - {candidate_name}"
    return send_email(to_email=candidate_email, subject=subject, html_body=html_body)

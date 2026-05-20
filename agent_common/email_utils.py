#agent_common/email_utils.py
import os
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .config import SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_HOST, SMTP_PORT, FRONTEND_URL

# ─────────────────────────────────────────────────────────
# Base email sender
# ─────────────────────────────────────────────────────────
def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not SMTP_USER or not SMTP_PASS:
        print(f"SMTP not configured – would send to {to_email}: {subject}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Hiersy <{SMTP_FROM}>"
        msg["To"] = to_email
        plain_text = "Please view this email in an HTML-capable client."
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        print(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        print(f"Email failed to {to_email}: {e}")
        return False

# ─────────────────────────────────────────────────────────
# Shared HTML wrapper & helpers
# ─────────────────────────────────────────────────────────
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
    """Reusable CTA button."""
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

# ─────────────────────────────────────────────────────────
# 1. Shortlisting notification (no test link)
# ─────────────────────────────────────────────────────────
def build_shortlist_email(
    candidate_name: str,
    job_title: str,
    hr_name: str = "Hiersy Team",
    has_test: bool = True,          # kept for legacy callers
) -> str:
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      We are pleased to inform you that your profile has been shortlisted for the position of
      <strong>{job_title}</strong> at Hiersy. After carefully reviewing your application, we were
      impressed with your background and experience, and we are excited to move forward with your
      candidature.
    </p>
    <p {bs}>
      Your skills and qualifications closely align with what we are looking for, and we believe
      you could be a valuable addition to our team.
    </p>
    <p {bs}>
      Our recruitment team will shortly connect with you regarding the next steps in the selection
      process, including interview details and further assessments, if applicable.
    </p>
    <p {bs}>
      Congratulations once again on being shortlisted. We appreciate your interest in building
      your career with Hiersy, and we look forward to interacting with you further.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# 2. Shortlisting test invite
# ─────────────────────────────────────────────────────────
def build_shortlist_with_test_email(
    candidate_name: str,
    job_title: str,
    test_link: str,
    hr_name: str = "Hiersy Team",
) -> str:
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      Further to your shortlisting for the position of <strong>{job_title}</strong>, we are excited
      to invite you to complete the next stage of our recruitment process — the
      <strong>Online Assessment Test</strong>.
    </p>
    <p {bs}>
      The assessment is designed to evaluate your skills and suitability for the role. We request
      you to complete the test within <strong>1 week</strong> from the date of receiving this email.
    </p>
    <p {bs}>Please find the test link below:</p>
    {_btn("Take Test", test_link)}
    <p {bs}>
      Kindly ensure that you complete the assessment within the given timeline, as applications
      submitted after the deadline may not be considered for further rounds.
    </p>
    <p {bs}>
      Should you have any questions or require assistance, please feel free to reach out to us.
    </p>
    <p {bs}>We appreciate your interest in Hiersy and wish you the very best for your assessment.</p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# legacy alias used by test service (port 8002)
def build_test_invite_email(
    candidate_name: str,
    job_title: str,
    token: str,
    duration: int,
    total_questions: int,
    hr_name: str = "Hiersy Team",
) -> str:
    test_url = f"{FRONTEND_URL}/test/{token}"
    return build_shortlist_with_test_email(candidate_name, job_title, test_url, hr_name)

# ─────────────────────────────────────────────────────────
# 3. Communication round invite
# ─────────────────────────────────────────────────────────
def build_communication_invite_email(
    candidate_name: str,
    job_title: str,
    test_link: str,
    hr_name: str = "Hiersy Team",
) -> str:
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      As part of the ongoing recruitment process for the position of <strong>{job_title}</strong>,
      we are pleased to invite you to participate in the <strong>Communication Assessment Round</strong>.
    </p>
    <p {bs}>
      This assessment is intended to evaluate your communication, comprehension, and professional
      interaction skills relevant to the role.
    </p>
    <p {bs}>
      Please complete the assessment within <strong>1 week</strong> from the date of receiving
      this email.
    </p>
    <p {bs}>You may access the assessment using the link provided below:</p>
    {_btn("Take Assessment", test_link)}
    <p {bs}>
      Kindly ensure that the assessment is completed within the specified timeline, as delayed
      submissions may not be considered for further stages of the hiring process.
    </p>
    <p {bs}>
      We appreciate your continued interest in opportunities with Hiersy and wish you the very
      best for your assessment.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# 4. Coding round invite
# ─────────────────────────────────────────────────────────
def build_coding_invite_email(
    candidate_name: str,
    job_title: str,
    token: str,
    duration: int,
    hr_name: str = "Hiersy Team",
) -> str:
    coding_url = f"{FRONTEND_URL}/coding/{token}"
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      We are pleased to inform you that you have successfully cleared the previous stage of our
      recruitment process for the position of <strong>{job_title}</strong>. Congratulations on
      your progress so far.
    </p>
    <p {bs}>
      As the next step in the selection process, you are invited to participate in the
      <strong>Coding Assessment Round</strong>. This assessment will help us further evaluate
      your technical and problem-solving capabilities relevant to the role.
    </p>
    <p {bs}>
      Please complete the coding test within <strong>1 week</strong> from the date of receiving
      this email.
    </p>
    <p {bs}>You may access the assessment using the link provided below:</p>
    {_btn("Take Coding Test", coding_url)}
    <p {bs}>
      We recommend attempting the assessment in a stable internet environment and ensuring
      completion within the stipulated timeline. Late submissions may not be considered for
      further evaluation.
    </p>
    <p {bs}>
      We appreciate your continued interest in Hiersy and wish you success in the upcoming round.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# 5. Live HR interview invite
# ─────────────────────────────────────────────────────────
def build_hr_invite_email(
    candidate_name: str,
    job_title: str,
    meet_url: str,
    scheduled_time: str = "",
    hr_name: str = "Hiersy Team",
) -> str:
    # Parse date/time if scheduled_time provided (expected: ISO or readable string)
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
    <p {bs}>
      We are pleased to inform you that you have successfully progressed to the final stage of
      our recruitment process for the position of <strong>{job_title}</strong>. Congratulations
      on making it this far.
    </p>
    <p {bs}>
      As the next and final step, we would like to invite you for an <strong>HR Discussion</strong>
      with our recruitment team to discuss your profile, role expectations, compensation details,
      and potential next steps.
    </p>
    <p {bs}>Please find the meeting details below:</p>
    <table cellpadding="0" cellspacing="0" style="margin:16px 0;border-left:3px solid #FF4400;padding-left:16px;">
      <tr><td style="font-size:14px;color:#555;padding:4px 0;"><strong>Date:</strong>&nbsp; {meeting_date}</td></tr>
      <tr><td style="font-size:14px;color:#555;padding:4px 0;"><strong>Time:</strong>&nbsp; {meeting_time}</td></tr>
      <tr><td style="font-size:14px;color:#555;padding:4px 0;"><strong>Platform:</strong>&nbsp; Jaas Meet</td></tr>
    </table>
    {_btn("Join Jaas Meet", meet_url)}
    <p {bs}>
      We request you to join the meeting a few minutes prior to the scheduled time and ensure
      a stable internet connection for a smooth discussion.
    </p>
    <p {bs}>
      Should you have any scheduling concerns, please feel free to reach out to us in advance.
    </p>
    <p {bs}>
      We look forward to speaking with you and appreciate your continued interest in Hiersy.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# 6. Outcome — Selected + BG verification
# ─────────────────────────────────────────────────────────
def build_selected_email(
    candidate_name: str,
    job_title: str,
    bg_verification_link: str = "",
    hr_name: str = "Hiersy Team",
) -> str:
    bs = _body_style()
    bg_section = ""
    if bg_verification_link:
        bg_section = f"""
        <p {bs}>
          As part of the final onboarding formalities, you are required to complete the
          <strong>Background Verification</strong> process. This process is mandatory prior to
          the issuance of your final onboarding confirmation.
        </p>
        <p {bs}>
          Please complete the verification process within <strong>1 week</strong> from the date
          of receiving this email.
        </p>
        <p {bs}>You may begin the process using the link below:</p>
        {_btn("Start Verification", bg_verification_link)}
        <p {bs}>
          Kindly ensure that all information and documents submitted during the verification
          process are accurate and valid. Delays in completion may impact the onboarding timeline.
        </p>
        """
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      We are delighted to inform you that you have been successfully selected for the position
      of <strong>{job_title}</strong> at Hiersy. Congratulations on your achievement and thank
      you for participating in our recruitment process.
    </p>
    <p {bs}>
      We were highly impressed with your overall performance throughout the selection stages,
      and we are excited about the possibility of having you join our team.
    </p>
    {bg_section}
    <p {bs}>
      Once again, congratulations on your selection. We look forward to welcoming you to Hiersy.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# 7. Offer letter with e-sign
# ─────────────────────────────────────────────────────────
def build_offer_letter_email(
    candidate_name: str,
    job_title: str,
    offer_link: str,
    esign_link: str,
    hr_name: str = "Hiersy Team",
) -> str:
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      We are pleased to formally extend to you an offer for the position of
      <strong>{job_title}</strong> at Hiersy. Congratulations on successfully completing our
      recruitment and evaluation process.
    </p>
    <p {bs}>
      After careful consideration of your profile and performance throughout the hiring stages,
      we are confident that you will be a valuable addition to our organization.
    </p>
    <p {bs}>Your Offer Letter is now available for review:</p>
    {_btn("View Offer Letter", offer_link)}
    <p {bs}>
      After reviewing the offer details, please proceed with the electronic signature process
      to confirm your acceptance:
    </p>
    {_btn("E-Sign Offer Letter", esign_link)}
    <p {bs}>
      We request you to complete the acceptance and e-sign process within the timeline mentioned
      in the offer document to ensure a smooth onboarding experience.
    </p>
    <p {bs}>
      Should you require any clarification regarding the offer or onboarding process, please
      feel free to contact us.
    </p>
    <p {bs}>
      We are excited to welcome you to Hiersy and look forward to a successful journey together.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# 8. Outcome — Not selected
# ─────────────────────────────────────────────────────────
def build_rejection_email(
    candidate_name: str,
    job_title: str,
    hr_name: str = "Hiersy Team",
) -> str:
    bs = _body_style()
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      Thank you for your time and effort during the recruitment process for the position of
      <strong>{job_title}</strong> at Hiersy.
    </p>
    <p {bs}>
      After careful consideration, we have decided to move forward with other candidates whose
      profiles more closely match our current requirements. This was not an easy decision, and
      we appreciate the interest you have shown in joining our team.
    </p>
    <p {bs}>
      We encourage you to keep an eye on our future openings and wish you the very best in
      your career journey.
    </p>
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

# ─────────────────────────────────────────────────────────
# Legacy combined outcome helper (used by live HR service)
# ─────────────────────────────────────────────────────────
def build_outcome_email(
    candidate_name: str,
    job_title: str,
    passed: bool,
    hr_name: str = "Hiersy Team",
) -> str:
    if passed:
        return build_selected_email(candidate_name, job_title, hr_name=hr_name)
    return build_rejection_email(candidate_name, job_title, hr_name=hr_name)



# ─────────────────────────────────────────────────────────
# 9. BGV Invitation email
# ─────────────────────────────────────────────────────────
def build_bgv_invite_email(
    candidate_name: str,
    candidate_email: str,
    token: str,
    hr_name: str = "Hiersy Team",
) -> str:
    """
    Build BGV invitation email content for candidate
    """
    bgv_link = f"{FRONTEND_URL}/back/{token}"  # Changed from /bgv/ to /back/
    bs = _body_style()
    
    body = f"""
    <p {bs}>Dear <strong>{candidate_name}</strong>,</p>
    <p {bs}>Greetings from Hiersy!</p>
    <p {bs}>
      Congratulations on successfully clearing all interview rounds for the position! 
      We are impressed with your performance throughout the selection process.
    </p>
    <p {bs}>
      As part of our final hiring process, you are required to complete the 
      <strong>Background Verification (BGV)</strong>. This is a mandatory step before 
      we proceed with the offer letter and onboarding process.
    </p>
    
    <h3 style="margin:24px 0 12px;color:#FF4400;">Documents Required:</h3>
    <ul style="margin:0 0 20px 20px;padding:0;line-height:1.8;">
      <li><strong>Identity Proof</strong> (any one): Aadhaar Card, PAN Card, or Passport</li>
      <li><strong>Education Documents</strong>: Degree Certificate</li>
      <li><strong>Employment Documents</strong>: Experience Letters from previous employers</li>
      <li><strong>Criminal Record</strong>: Self-declaration affidavit</li>
    </ul>
    
    <h3 style="margin:24px 0 12px;color:#FF4400;">Face Verification Required:</h3>
    <p {bs}>
      You will also need to take a photo holding your ID document next to your face 
      for identity verification purposes.
    </p>
    
    <h3 style="margin:24px 0 12px;color:#FF4400;">Important Notes:</h3>
    <ul style="margin:0 0 20px 20px;padding:0;line-height:1.8;">
      <li><strong>Deadline:</strong> Please submit all documents within 5 days</li>
      <li>Documents will be verified using AI and may be reviewed by HR</li>
      <li>Ensure all uploaded documents are clear and legible</li>
      <li>Incomplete or unclear submissions may cause delays</li>
    </ul>
    
    <p {bs}>Please click the button below to start your background verification:</p>
    {_btn("Start Background Verification", bgv_link)}
    
    <p {bs}>
      If you have any questions or face any issues during the submission process, 
      please don't hesitate to reach out to our HR team.
    </p>
    
    <p {bs}>
      <strong>Note:</strong> Your application will be considered complete only after 
      successful BGV verification. We appreciate your cooperation in this matter.
    </p>
    
    {_signature(hr_name)}
    """
    return wrap_email_body(body)

def send_bgv_invite_email(
    candidate_name: str,
    candidate_email: str,
    token: str,
    hr_name: str = "Hiersy Team",
) -> bool:
    """
    Send BGV invitation email to candidate
    """
    html_body = build_bgv_invite_email(candidate_name, candidate_email, token, hr_name)
    subject = f"Background Verification Required - {candidate_name}"
    
    return send_email(
        to_email=candidate_email,
        subject=subject,
        html_body=html_body
    )
import os
import smtplib
import ssl
import logging
from email.message import EmailMessage

logger = logging.getLogger("mail")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "MedNova VR")

MAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def send_mail(to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """
    Sends one email. Returns True if it was sent, False otherwise.
    Never raises - a mail failure should not break the calling request
    (e.g. approving a therapist should still succeed even if the mail
    server is briefly down). Failures are logged instead.
    """
    if not MAIL_ENABLED:
        logger.warning(
            "MAIL not configured (SMTP_HOST/SMTP_USER/SMTP_PASS missing) - "
            "skipping email to %s with subject %r", to_email, subject,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
    msg["To"] = to_email
    msg.set_content(text_body or _strip_html(html_body))
    msg.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info("Mail sent to %s | subject=%r", to_email, subject)
        return True
    except Exception:
        logger.exception("Failed to send mail to %s | subject=%r", to_email, subject)
        return False


def _strip_html(html: str) -> str:
    """Very small fallback so plain-text mail clients don't get raw HTML tags."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()

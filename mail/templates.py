"""
Email subject + HTML body builders.
Keep these as plain functions so mailer.py stays provider-agnostic.
"""

import os

LOGIN_URL = os.getenv("APP_BASE_URL", "https://domain-or-ip").rstrip("/") + "/login"
RESET_PASSWORD_URL = os.getenv("APP_BASE_URL", "https://domain-or-ip").rstrip("/") + "/reset-password"


def email_verification_otp_email(full_name: str, otp: str, expire_minutes: int) -> tuple[str, str]:
    """Returns (subject, html_body) for the 'verify your email' OTP message."""
    subject = "Verify your email for MedNova VR"

    html_body = f"""\
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
  <h2 style="color: #1B2A4A;">Confirm your email, {full_name}</h2>
  <p>
    Use the code below to verify this email address on your
    <strong>MedNova VR</strong> account. It expires in {expire_minutes} minutes.
  </p>
  <p style="text-align: center; margin: 28px 0;">
    <span style="display:inline-block;font-size:32px;letter-spacing:8px;
                 font-weight:bold;color:#1B2A4A;background:#EEF1F5;
                 padding:14px 24px;border-radius:8px;">
      {otp}
    </span>
  </p>
  <p style="color:#5A6472;font-size:13px;">
    If you didn't try to register on MedNova VR, you can safely ignore this email.
    Never share this code with anyone.
  </p>
</div>
"""
    return subject, html_body


def password_reset_email(full_name: str, reset_token: str, expire_minutes: int) -> tuple[str, str]:
    """Returns (subject, html_body) for the 'reset your password' email."""
    subject = "Reset your MedNova VR password"
    reset_link = f"{RESET_PASSWORD_URL}?token={reset_token}"

    html_body = f"""\
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
  <h2 style="color: #1B2A4A;">Reset your password, {full_name}</h2>
  <p>
    We received a request to reset the password on your
    <strong>MedNova VR</strong> account. This link expires in {expire_minutes} minutes.
  </p>
  <p style="text-align: center; margin: 28px 0;">
    <a href="{reset_link}"
       style="background:#1B2A4A;color:#fff;padding:10px 20px;
              border-radius:6px;text-decoration:none;">
      Reset password
    </a>
  </p>
  <p style="color:#5A6472;font-size:13px;">
    If you didn't request this, you can safely ignore this email — your
    password will not be changed. Never share this link with anyone.
  </p>
</div>
"""
    return subject, html_body


def registration_approved_email(full_name: str) -> tuple[str, str]:
    """Returns (subject, html_body) for the 'account approved' email."""
    subject = "Your MedNova VR account has been approved"

    html_body = f"""\
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
  <h2 style="color: #1B2A4A;">You're approved, {full_name}!</h2>
  <p>
    Good news — your therapist account on <strong>MedNova VR</strong> has been
    reviewed and approved by our Super Admin team.
  </p>
  <p>
    You can now log in and start using your dashboard:
  </p>
  <p style="text-align: center; margin: 24px 0;">
    <a href="{LOGIN_URL}"
       style="background:#1B2A4A;color:#fff;padding:10px 20px;
              border-radius:6px;text-decoration:none;">
      Log in to MedNova VR
    </a>
  </p>
  <p style="color:#5A6472;font-size:13px;">
    If you weren't expecting this, you can ignore this email.
  </p>
</div>
"""
    return subject, html_body
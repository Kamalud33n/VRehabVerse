import datetime

from fastapi import APIRouter, HTTPException, Request

from database import get_db
from models import User, EmailVerificationToken, AuditLog
from auth.schemas import VerifyEmailRequest, ResendVerificationRequest
from auth.security import (
    generate_otp, hash_otp, verify_otp,
    EMAIL_OTP_EXPIRE_MINUTES, EMAIL_OTP_MAX_ATTEMPTS, EMAIL_OTP_RESEND_COOLDOWN_SECONDS,
    IS_DEV_ENV,
)
from auth.rate_limit import limiter, OTP_VERIFY_RATE_LIMIT, OTP_RESEND_RATE_LIMIT
from mail.mailer import send_mail
from mail.templates import email_verification_otp_email

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_INVALID_CODE = "Invalid or expired verification code."


# EMAIL_OTP_MAX_ATTEMPTS already caps guesses against any *single* token,
# but nothing stopped a script from just calling resend-verification-email
# in a loop (past its own cooldown) to keep generating fresh 5-attempt
# windows. This IP-level ceiling bounds total guesses across all tokens.
@router.post("/verify-email")
@limiter.limit(OTP_VERIFY_RATE_LIMIT)
def verify_email(request: Request, payload: VerifyEmailRequest):
    email = payload.email.strip().lower()

    with get_db() as db:
        user = db.query(User).filter(User.email == email, User.is_deleted == False).first()
        if not user:
            # Same generic error whether the email exists or not, so this
            # endpoint can't be used to enumerate registered accounts.
            raise HTTPException(status_code=400, detail=GENERIC_INVALID_CODE)

        if user.email_verified:
            return {"status": "ok", "message": "This email is already verified. You can log in once your account is approved."}

        token = (
            db.query(EmailVerificationToken)
            .filter(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used == False)
            .order_by(EmailVerificationToken.created_at.desc())
            .first()
        )

        if not token or token.expires_at < datetime.datetime.utcnow():
            raise HTTPException(status_code=400, detail="This code has expired. Please request a new one.")

        if token.attempts >= EMAIL_OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=400, detail="Too many incorrect attempts. Please request a new code.")

        if not verify_otp(payload.otp.strip(), token.otp_hash):
            token.attempts += 1
            db.commit()
            remaining = max(EMAIL_OTP_MAX_ATTEMPTS - token.attempts, 0)
            raise HTTPException(
                status_code=400,
                detail=f"Incorrect verification code. {remaining} attempt(s) remaining." if remaining
                       else "Incorrect verification code. Please request a new one.",
            )

        token.used = True
        user.email_verified = True
        user.email_verified_at = datetime.datetime.utcnow()
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="email_verified", performed_by=user.id))
        db.commit()

        return {
            "status": "ok",
            "message": "Email verified successfully. Your account will need Super Admin approval before you can log in.",
        }


# The existing EMAIL_OTP_RESEND_COOLDOWN_SECONDS check below only spaces
# out *consecutive* resends by 60s each - it doesn't cap how many times
# this can be called over an hour, so it's still spammable at ~1/min
# forever (mailbox flooding, SMTP quota burn, and re-farming fresh OTP
# attempt windows). This adds that ceiling.
@router.post("/resend-verification-email")
@limiter.limit(OTP_RESEND_RATE_LIMIT)
def resend_verification_email(request: Request, payload: ResendVerificationRequest):
    email = payload.email.strip().lower()
    generic_response = {"status": "ok", "message": "If that email is registered and not yet verified, a new code has been sent."}

    with get_db() as db:
        user = db.query(User).filter(User.email == email, User.is_deleted == False).first()

        # Don't reveal whether the account exists or is already verified —
        # always return the same generic response either way.
        if not user or user.email_verified:
            return generic_response

        last_token = (
            db.query(EmailVerificationToken)
            .filter(EmailVerificationToken.user_id == user.id)
            .order_by(EmailVerificationToken.created_at.desc())
            .first()
        )
        if last_token:
            elapsed = (datetime.datetime.utcnow() - last_token.created_at).total_seconds()
            if elapsed < EMAIL_OTP_RESEND_COOLDOWN_SECONDS:
                return generic_response
            if not last_token.used:
                last_token.used = True  # invalidate the previous code once a new one is issued

        otp = generate_otp()
        db.add(EmailVerificationToken(
            user_id=user.id,
            email=user.email,
            otp_hash=hash_otp(otp),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=EMAIL_OTP_EXPIRE_MINUTES),
        ))
        db.commit()

        subject, html_body = email_verification_otp_email(user.full_name, otp, EMAIL_OTP_EXPIRE_MINUTES)
        mail_sent = send_mail(user.email, subject, html_body)

        if not mail_sent:
            if IS_DEV_ENV:
                resp = dict(generic_response)
                resp["dev_otp"] = otp
                resp["dev_note"] = "APP_ENV=development - email delivery is not configured, so this code is returned directly for local testing only."
                return resp
            # Production: don't leak the code, just log it server-side
            # (mail/mailer.py already logs the failed-send warning) and
            # return the same generic response as the success path so
            # this endpoint still can't be used to probe account state.
            return generic_response

        return generic_response
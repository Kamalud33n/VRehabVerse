import datetime
import secrets

from fastapi import APIRouter, Request

from database import get_db
from models import User, PasswordResetToken, AuditLog
from auth.schemas import ForgotPasswordRequest
from auth.security import RESET_TOKEN_EXPIRE_MINUTES, IS_DEV_ENV
from auth.rate_limit import limiter, FORGOT_PASSWORD_RATE_LIMIT
from mail.mailer import send_mail
from mail.templates import password_reset_email

router = APIRouter(prefix="/auth", tags=["auth"])


# The reset token itself (secrets.token_urlsafe(32)) isn't brute-forceable,
# but without a rate limit this endpoint could be called in a tight loop
# to flood a victim's inbox or burn SMTP send quota. IP-capped, not
# email-capped, so it can't be used to selectively lock a specific victim
# out of requesting resets.
@router.post("/forgot-password")
@limiter.limit(FORGOT_PASSWORD_RATE_LIMIT)
def forgot_password(request: Request, payload: ForgotPasswordRequest):
    """
    Creates a valid, time-limited reset token and emails it as a link (see
    mail/templates.py:password_reset_email). If SMTP isn't configured, the
    token is NEVER put in the response unless APP_ENV=development is set
    on purpose - see auth/security.py:IS_DEV_ENV. Silently leaking a
    working password-reset token to whoever calls this endpoint would be a
    full account-takeover bug, not a convenience.
    """
    with get_db() as db:
        user = db.query(User).filter(User.email == payload.email.lower(), User.is_deleted == False).first()

        # Always return the same generic response whether or not the email
        # exists, so this endpoint can't be used to enumerate registered
        # emails. The token is only actually created if the user exists.
        generic_response = {"status": "ok", "message": "If that email is registered, a reset link has been generated."}
        if not user:
            return generic_response

        token = secrets.token_urlsafe(32)
        reset = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
        )
        db.add(reset)
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="forgot_password_requested",
                         performed_by=user.id))
        db.commit()

        subject, html_body = password_reset_email(user.full_name, token, RESET_TOKEN_EXPIRE_MINUTES)
        mail_sent = send_mail(user.email, subject, html_body)

        if not mail_sent and IS_DEV_ENV:
            resp = dict(generic_response)
            resp["dev_reset_token"] = token
            resp["dev_note"] = "APP_ENV=development - email delivery is not configured, so this token is returned directly for local testing only."
            return resp

        return generic_response
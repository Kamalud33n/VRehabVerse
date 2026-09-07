import datetime

from fastapi import APIRouter, HTTPException, Request

from database import get_db
from models import User, PasswordResetToken, AuditLog
from auth.schemas import ResetPasswordRequest
from auth.security import hash_password
from auth.rate_limit import limiter, RESET_PASSWORD_RATE_LIMIT

router = APIRouter(prefix="/auth", tags=["auth"])


# `token` is a 256-bit CSPRNG value, so this isn't guessable - the limit
# here is against request-flood/DoS on the endpoint, not token brute force.
@router.post("/reset-password")
@limiter.limit(RESET_PASSWORD_RATE_LIMIT)
def reset_password(request: Request, payload: ResetPasswordRequest):
    with get_db() as db:
        reset = db.query(PasswordResetToken).filter(PasswordResetToken.token == payload.token).first()

        if not reset or reset.used or reset.expires_at < datetime.datetime.utcnow():
            raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

        user = db.query(User).filter(User.id == reset.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Account not found")

        user.password_hash = hash_password(payload.new_password)
        reset.used = True
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="password_reset", performed_by=user.id))
        db.commit()

        return {"status": "ok", "message": "Password updated. You can now log in with your new password."}
import datetime

from fastapi import APIRouter, HTTPException, Response, Request

from database import get_db
from models import User, AuditLog
from auth.schemas import LoginRequest, UserOut
from auth.security import verify_password, create_access_token, COOKIE_NAME, ACCESS_TOKEN_EXPIRE_MINUTES, IS_DEV_ENV
from auth.dependencies import get_current_user_optional
from auth.rate_limit import limiter, LOGIN_RATE_LIMIT

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_MAX_AGE = ACCESS_TOKEN_EXPIRE_MINUTES * 60


def _user_out(user: User) -> dict:
    data = UserOut.model_validate(user).model_dump()
    data["hospital_name"] = user.hospital.name if user.hospital else None
    return data


# Per-IP cap on password attempts - previously unlimited, so a single
# script could brute-force any account's password directly against this
# endpoint with no lockout of any kind. See auth/rate_limit.py for why
# this is IP-keyed and why 8/minute (LOGIN_RATE_LIMIT).
@router.post("/login")
@limiter.limit(LOGIN_RATE_LIMIT)
def login(request: Request, payload: LoginRequest, response: Response):
    with get_db() as db:
        user = db.query(User).filter(User.email == payload.email.lower(), User.is_deleted == False).first()

        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not user.is_active:
            raise HTTPException(status_code=403, detail="This account has been deactivated. Contact your administrator.")

        if not user.email_verified:
            raise HTTPException(
                status_code=403,
                detail="Please verify your email before logging in. Check your inbox for the verification code, "
                       "or request a new one.",
            )

        if user.role == "therapist" and user.status == "pending":
            raise HTTPException(status_code=403, detail="Your registration is still pending Super Admin approval.")
        if user.role == "therapist" and user.status == "rejected":
            raise HTTPException(status_code=403, detail="Your registration was rejected. Contact support for details.")

        user.last_login_at = datetime.datetime.utcnow()
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="login", performed_by=user.id,
                         details={"email": user.email}))
        db.commit()
        db.refresh(user)

        token = create_access_token(user_id=user.id, role=user.role)
        out = _user_out(user)

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        # secure=True whenever APP_ENV isn't "development" - matches
        # IS_DEV_ENV used everywhere else (register/verify/forgot-password)
        # so this doesn't need its own env var. If you ARE serving prod
        # over plain HTTP behind something that terminates TLS upstream,
        # this is still correct since the cookie only needs "secure" for
        # the browser<->edge hop; if you're genuinely serving prod over
        # plain HTTP end-to-end, fix that instead of loosening this.
        secure=not IS_DEV_ENV,
    )
    return {"status": "ok", "user": out}


@router.post("/logout")
def logout(response: Response, request: Request):
    user = get_current_user_optional(request)
    if user:
        with get_db() as db:
            db.add(AuditLog(entity_type="User", entity_id=user.id, action="logout", performed_by=user.id))
            db.commit()
    response.delete_cookie(COOKIE_NAME)
    return {"status": "logged_out"}


@router.get("/me")
def me(request: Request):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _user_out(user)
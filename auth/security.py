import os
import hmac
import hashlib
import secrets
import datetime

from passlib.context import CryptContext
from jose import jwt, JWTError

# Explicit environment flag - defaults to "production" (fail-safe) so that
# dev-only behaviour (see register.py / verify_email.py / forgot_password.py:
# returning the OTP / reset token directly in the API response when SMTP
# isn't configured) NEVER turns on just because someone forgot to set up
# SMTP_HOST/USER/PASS on a real deployment. It only activates when this is
# set to "development" on purpose.
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
IS_DEV_ENV = APP_ENV in ("development", "dev", "local")

# JWT_SECRET_KEY: only allow the insecure fallback in dev. If it's missing
# in production, crash on startup instead of silently signing every login
# token with a hardcoded, source-controlled key - a print()ed warning is
# easy to miss in server logs and this has real account-takeover impact
# (anyone can forge a valid session cookie for any user/role).
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    if IS_DEV_ENV:
        JWT_SECRET_KEY = "dev-only-insecure-secret-change-me"
        print("[auth] WARNING: JWT_SECRET_KEY not set in environment - using an "
              "insecure dev-only default because APP_ENV=development.")
    else:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Refusing to start with APP_ENV="
            f"'{APP_ENV}' and no JWT secret - set JWT_SECRET_KEY in your "
            "environment/.env before deploying."
        )

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
COOKIE_NAME = "access_token"

RESET_TOKEN_EXPIRE_MINUTES = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", "30"))

# ---------------------------------------------------------------------------
# Email verification (OTP) tunables.
# ---------------------------------------------------------------------------
EMAIL_OTP_LENGTH = 6
EMAIL_OTP_EXPIRE_MINUTES = int(os.getenv("EMAIL_OTP_EXPIRE_MINUTES", "15"))
EMAIL_OTP_MAX_ATTEMPTS = int(os.getenv("EMAIL_OTP_MAX_ATTEMPTS", "5"))
EMAIL_OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("EMAIL_OTP_RESEND_COOLDOWN_SECONDS", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(plain_password, password_hash)
    except Exception:
        return False


def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Email verification OTP helpers.
# Codes are generated with `secrets` (CSPRNG) and only ever stored as an
# HMAC-SHA256 digest — the plaintext code exists only in memory long enough
# to be emailed to the user, never in the database or logs.
# ---------------------------------------------------------------------------
def generate_otp(length: int = EMAIL_OTP_LENGTH) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp(code: str) -> str:
    return hmac.new(JWT_SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def verify_otp(code: str, otp_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code), otp_hash)
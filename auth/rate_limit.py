import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# ---------------------------------------------------------------------------
# Shared rate limiter for the auth endpoints (login, OTP verify/resend,
# forgot/reset password). These are the only unauthenticated, credential-
# guessing surfaces in the app - no session cookie exists yet to key a
# limit off of - so requests are throttled per client IP via slowapi
# (in-memory storage). That's a deliberate fit for how this app is
# deployed: a single `web` container in docker-compose.yml with no Redis,
# so an in-memory limiter needs no extra infra. If this ever moves to
# multiple app instances behind a load balancer, point slowapi at a shared
# backend instead (e.g. Limiter(storage_uri="redis://...")) - otherwise
# each instance keeps its own counters and the effective limit multiplies
# by the number of instances.
#
# IP-based limiting stops the common case (a script hammering one endpoint
# from one place) but not a distributed attacker rotating IPs. It's paired
# with what already existed server-side per-account: verify-email's
# per-token EMAIL_OTP_MAX_ATTEMPTS cap, and reset/verify tokens being
# large CSPRNG values that aren't practically guessable even without any
# rate limit. Rate limiting here closes the remaining gap: unlimited
# *password* guesses on /auth/login, and unlimited OTP *resends* to farm
# fresh five-attempt windows on /auth/resend-verification-email.
# headers_enabled stays at its default (False) on purpose. Turning it on
# would have slowapi try to attach X-RateLimit-*/Retry-After headers to
# every response the limiter sees, including ones inside the limit - not
# just the 429s - which requires the endpoint to return a real Response
# object. Every auth endpoint here returns a plain dict (FastAPI wraps
# that into a Response afterwards, after slowapi's decorator has already
# run), so headers_enabled=True raises on every *successful* login/OTP/
# reset call, not just throttled ones - confirmed by testing against this
# codebase directly. The 429 body still tells the client it was rate
# limited; it just won't carry a machine-readable Retry-After header.
limiter = Limiter(key_func=get_remote_address)

# Tunable via env so ops can loosen/tighten these without a code change,
# same pattern as ACCESS_TOKEN_EXPIRE_MINUTES etc. in auth/security.py.
# Defaults are deliberately tight for login (the actual password-guessing
# surface) and looser-but-still-capped for the email-sending endpoints
# (their own per-token cooldown/attempt caps already do most of the work;
# this is a ceiling against total spam volume, not the primary defense).
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "8/minute")
OTP_VERIFY_RATE_LIMIT = os.getenv("OTP_VERIFY_RATE_LIMIT", "10/minute")
OTP_RESEND_RATE_LIMIT = os.getenv("OTP_RESEND_RATE_LIMIT", "5/hour")
FORGOT_PASSWORD_RATE_LIMIT = os.getenv("FORGOT_PASSWORD_RATE_LIMIT", "5/hour")
RESET_PASSWORD_RATE_LIMIT = os.getenv("RESET_PASSWORD_RATE_LIMIT", "10/hour")

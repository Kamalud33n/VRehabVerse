from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import joinedload

from database import get_db
from models import User
from auth.security import decode_access_token, COOKIE_NAME


def _get_token_from_request(request: Request) -> str | None:
    # Cookie first (browser pages), then Authorization header (API clients).
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:]
    return None


def get_current_user_optional(request: Request) -> User | None:
    """Returns the authenticated User, or None if not logged in / token invalid.
    Never raises - use this in page routes where you want to redirect
    manually instead of getting a raw 401."""
    token = _get_token_from_request(request)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    with get_db() as db:
        user = (
            db.query(User)
            .options(joinedload(User.hospital))
            .filter(User.id == user_id, User.is_deleted == False)
            .first()
        )
        if user:
            db.expunge(user)
        return user


def get_current_user(request: Request) -> User:
    """Strict dependency for API routes - raises 401 if not authenticated."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not user.email_verified:
        # Defense in depth: login already blocks unverified accounts, but
        # a still-valid older session token (e.g. issued before this
        # requirement existed) shouldn't stay authenticated either.
        raise HTTPException(status_code=403, detail="Email not verified")
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Account is pending approval")
    return user


def require_roles(*roles: str):
    """Dependency factory: require_roles('super_admin') or
    require_roles('super_admin', 'therapist')."""
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission to access this resource")
        return user
    return _dep


require_super_admin = require_roles("super_admin")
require_therapist = require_roles("therapist")


def get_current_user_for_page(request: Request) -> User | None | RedirectResponse:
    """
    Use inside page (HTML) routes. Returns the User if logged in and
    approved, or a RedirectResponse to /login if not - the caller should
    check `isinstance(result, RedirectResponse)` and return it directly.
    """
    user = get_current_user_optional(request)
    if not user or not user.is_active or not user.email_verified or not user.is_approved:
        return RedirectResponse(url="/login", status_code=303)
    return user
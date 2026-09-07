from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import templates
from auth.dependencies import get_current_user_optional, get_current_user_for_page

router = APIRouter()

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _home_for(user) -> str:
    return "/admin/dashboard" if user.role == "super_admin" else "/dashboard"


# ---------------------------------------------------------------------------
# Public pages - landing / auth. If someone is already logged in and lands
# here anyway (e.g. bookmarked /login), send them straight to their app
# instead of showing the auth forms again.
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def page_index(request: Request):
    user = get_current_user_optional(request)
    if user and user.is_active and user.is_approved:
        return RedirectResponse(url=_home_for(user))
    return templates.TemplateResponse(request, "auth/index.html", headers=NO_CACHE_HEADERS)


@router.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    user = get_current_user_optional(request)
    if user and user.is_active and user.is_approved:
        return RedirectResponse(url=_home_for(user))
    return templates.TemplateResponse(request, "auth/login.html", headers=NO_CACHE_HEADERS)


@router.get("/register", response_class=HTMLResponse)
async def page_register(request: Request):
    user = get_current_user_optional(request)
    if user and user.is_active and user.is_approved:
        return RedirectResponse(url=_home_for(user))
    return templates.TemplateResponse(request, "auth/register.html", headers=NO_CACHE_HEADERS)


@router.get("/forgot-password", response_class=HTMLResponse)
async def page_forgot_password(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html", headers=NO_CACHE_HEADERS)


@router.get("/reset-password", response_class=HTMLResponse)
async def page_reset_password(request: Request):
    return templates.TemplateResponse(request, "auth/reset_password.html", headers=NO_CACHE_HEADERS)


# ---------------------------------------------------------------------------
# Therapist / Specialist app pages - login required. get_current_user_for_page
# redirects to /login if not authenticated, active, and approved.
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app/dashboard.html", headers=NO_CACHE_HEADERS)


@router.get("/patients", response_class=HTMLResponse)
async def page_patients(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app/patients.html", headers=NO_CACHE_HEADERS)


@router.get("/session", response_class=HTMLResponse)
async def page_session(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app/session.html", headers=NO_CACHE_HEADERS)


@router.get("/reports", response_class=HTMLResponse)
async def page_reports(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app/reports.html", headers=NO_CACHE_HEADERS)


@router.get("/analytics", response_class=HTMLResponse)
async def page_analytics(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app/analytics.html", headers=NO_CACHE_HEADERS)


@router.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app/settings.html", headers=NO_CACHE_HEADERS)


# /profile is kept as a redirect so old bookmarks / links still work - the
# therapist-facing Personal details / Security / Hospital / Support pages
# now all live together under the single "Settings" sidebar entry.
@router.get("/profile", response_class=HTMLResponse)
async def page_profile(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return RedirectResponse(url="/settings", status_code=303)


# ---------------------------------------------------------------------------
# Super Admin pages - login required AND role must be super_admin. A
# therapist hitting these gets redirected to their own dashboard rather
# than an ugly 403 page.
# ---------------------------------------------------------------------------

def _require_admin_page(request: Request):
    user = get_current_user_for_page(request)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != "super_admin":
        return RedirectResponse(url="/dashboard", status_code=303)
    return user


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def page_admin_dashboard(request: Request):
    user = _require_admin_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "admin/admin_dashboard.html", headers=NO_CACHE_HEADERS)


@router.get("/admin/registrations", response_class=HTMLResponse)
async def page_admin_registrations(request: Request):
    user = _require_admin_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "admin/admin_registrations.html", headers=NO_CACHE_HEADERS)


@router.get("/admin/customers", response_class=HTMLResponse)
async def page_admin_customers(request: Request):
    user = _require_admin_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "admin/admin_customers.html", headers=NO_CACHE_HEADERS)


@router.get("/admin/profile", response_class=HTMLResponse)
async def page_admin_profile(request: Request):
    user = _require_admin_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "admin/admin_profile.html", headers=NO_CACHE_HEADERS)


@router.get("/admin/support", response_class=HTMLResponse)
async def page_admin_support(request: Request):
    user = _require_admin_page(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "admin/admin_support.html", headers=NO_CACHE_HEADERS)
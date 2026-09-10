import os
import datetime

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from database import get_db, init_db
from config import STATIC_DIR, UPLOADS_DIR, ASSETS_DIR
from models import Patient, Exercise, ExerciseCategory, Setting
from services.vr_ws_manager import vr_manager, dashboard_manager
from auth.rate_limit import limiter

from routers import pages, ws
from routers.clinical import patients, sessions, dashboard, analytics, reports, export
from routers.account import admin, profile, support
from auth import login as auth_login, register as auth_register
from auth import forgot_password as auth_forgot_password, reset_password as auth_reset_password
from auth import verify_email as auth_verify_email

init_db()

app = FastAPI(title="MedNova VR Rehabilitation Platform", version="1.0.0")


class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
    """
    HTML responses (session.html, dashboard pages, etc. - served via
    routers/pages.py or any other route) get Cache-Control: no-cache,
    must-revalidate. This makes the browser re-validate with the server
    on every load instead of silently reusing a stale cached copy, which
    was causing the "session connection looks cached, hard refresh fixes
    it" issue locally - and would do the same (often worse, behind a CDN/
    reverse proxy with longer default caching) in production if left as
    is. Static assets under /static, /uploads, /assets are untouched
    here since they're served by StaticFiles, not affected by this check
    (content-type for those isn't text/html), and can use normal/longer
    caching safely.
    """
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.add_middleware(NoCacheHTMLMiddleware)

# Powers the @limiter.limit(...) decorators on the login/OTP/password-reset
# routes (see auth/rate_limit.py for why these specific endpoints and why
# an in-memory, per-IP limiter fits this deployment). app.state.limiter and
# the exception handler are required by slowapi for @limiter.limit to work
# at all; SlowAPIMiddleware is what actually attaches the Retry-After
# header to the 429 response.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Reads the deployed frontend/VR domain(s) from the environment instead of
# a hardcoded value here — set ALLOWED_ORIGINS in .env, comma-separated for
# more than one (e.g. "https://vr-backend.ourdomain.com,http://localhost:8000").
# Falls back to localhost only, so a missing env var fails safe (nothing
# else gets allowed) rather than accidentally trusting a stale placeholder.
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000")
ALLOWED_ORIGINS = [origin.strip() for origin in _allowed_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# Auth must be included first - other routers/pages depend on the
# session cookie these endpoints issue.
app.include_router(auth_login.router)
app.include_router(auth_register.router)
app.include_router(auth_verify_email.router)
app.include_router(auth_forgot_password.router)
app.include_router(auth_reset_password.router)

app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(profile.router)
app.include_router(support.router)
app.include_router(patients.router)
app.include_router(sessions.router)
app.include_router(dashboard.router)
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(ws.router)
app.include_router(export.router)


@app.on_event("startup")
async def seed():
    if os.getenv("SEED_DEMO_DATA", "false").lower() != "true":
        return
    with get_db() as db:
        if db.query(Patient).count() > 0:
            return

        category = ExerciseCategory(name="Upper Limb", description="Shoulder, elbow, wrist exercises")
        db.add(category)
        db.flush()

        db.add(Exercise(category_id=category.id, name="Shoulder Flexion", target_body_part="Shoulder",
                         default_target_reps=15, default_target_rom=150))

        patients = [
            Patient(name="John Smith", age=45, gender="Male", weight=82.5, height=178.0,
                    diagnosis="Rotator Cuff Tear", affected_body_part="Right Shoulder",
                    doctor_name="Dr. Sarah Johnson", therapist_name="Michael Brown",
                    phone="+1 (555) 123-4567", email="john.smith@email.com"),
            Patient(name="Maria Garcia", age=62, gender="Female", weight=68.0, height=165.0,
                    diagnosis="Knee Osteoarthritis", affected_body_part="Left Knee",
                    doctor_name="Dr. Robert Chen", therapist_name="Lisa Wong",
                    phone="+1 (555) 234-5678", email="maria.garcia@email.com"),
        ]
        db.add_all(patients)
        db.flush()

        for key, val, desc in [
            ("session_code_ttl_minutes", "15", "Minutes a session code stays valid"),
            ("heartbeat_interval_seconds", "10", "VR WebSocket heartbeat interval"),
        ]:
            db.add(Setting(key=key, value=val, description=desc))

        db.commit()
        print("Seed data inserted.")


@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.datetime.now().isoformat(),
        "vr_connections": len(vr_manager.active_sessions),
        "dashboard_connections": len(dashboard_manager.connections),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)

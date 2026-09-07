import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy import func

from database import get_db
from models import SessionModel, LiveMetric, Patient, DeviceTelemetry, User
from auth.dependencies import get_current_user
from services.game_metrics import get_fields_for

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _owned_patient_ids(db, current_user: User) -> list[str]:
    """All patient IDs belonging to this therapist. Only meaningful for
    role == 'therapist' — callers should check the role first."""
    return [p.id for p in db.query(Patient.id).filter(Patient.responsible_therapist_id == current_user.id)]


def _check_patient_access(db, patient_id: str, current_user: User) -> Patient:
    """Raises 404 if the patient doesn't exist, or belongs to a different
    therapist. Used any time a patient_id arrives as a query/path param,
    so a therapist can't pull another therapist's patient data just by
    typing a different id."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
        raise HTTPException(404, "Patient not found")
    return patient


def _scope_to_patient(q, db, current_user: User, patient_id: str | None):
    """Applies patient_id filter to a SessionModel query, with ownership
    enforcement: an explicit patient_id is checked against the current
    therapist; with no patient_id, a therapist's query is scoped to only
    their own patients' sessions (instead of the whole org's)."""
    if patient_id:
        _check_patient_access(db, patient_id, current_user)
        return q.filter(SessionModel.patient_id == patient_id)
    if current_user.role == "therapist":
        return q.filter(SessionModel.patient_id.in_(_owned_patient_ids(db, current_user)))
    return q


@router.get("/daily-sessions")
def daily_sessions(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
                    current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = (
            db.query(func.date(SessionModel.created_at), func.count(SessionModel.id))
            .filter(SessionModel.created_at >= since, SessionModel.is_deleted == False)
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date(SessionModel.created_at))
            .order_by(func.date(SessionModel.created_at))
            .all()
        )
        return [{"date": str(d), "count": c} for d, c in rows]


@router.get("/monthly-sessions")
def monthly_sessions(months: int = Query(12, ge=1, le=36), patient_id: str | None = None,
                      current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=months * 30)
        q = (
            db.query(
                func.date_format(SessionModel.created_at, "%Y-%m"),
                func.count(SessionModel.id),
            )
            .filter(SessionModel.created_at >= since, SessionModel.is_deleted == False)
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date_format(SessionModel.created_at, "%Y-%m"))
            .order_by(func.date_format(SessionModel.created_at, "%Y-%m"))
            .all()
        )
        return [{"month": m, "count": c} for m, c in rows]


@router.get("/accuracy-trend")
def accuracy_trend(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
                    current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = (
            db.query(func.date(SessionModel.created_at), func.avg(SessionModel.final_accuracy))
            .filter(
                SessionModel.created_at >= since,
                SessionModel.final_accuracy != None,
                SessionModel.is_deleted == False,
            )
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date(SessionModel.created_at))
            .order_by(func.date(SessionModel.created_at))
            .all()
        )
        return [{"date": str(d), "avg_accuracy": round(a, 1)} for d, a in rows]


@router.get("/rom-trend")
def rom_trend(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
               current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = (
            db.query(func.date(SessionModel.created_at), func.avg(SessionModel.final_rom))
            .filter(
                SessionModel.created_at >= since,
                SessionModel.final_rom != None,
                SessionModel.is_deleted == False,
            )
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date(SessionModel.created_at))
            .order_by(func.date(SessionModel.created_at))
            .all()
        )
        return [{"date": str(d), "avg_rom": round(r, 1)} for d, r in rows]


@router.get("/completion-rate-trend")
def completion_rate_trend(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
                           current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = (
            db.query(
                func.date(SessionModel.created_at),
                func.count(SessionModel.id),
                func.sum(func.if_(SessionModel.status == "completed", 1, 0)),
            )
            .filter(SessionModel.created_at >= since, SessionModel.is_deleted == False)
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date(SessionModel.created_at))
            .order_by(func.date(SessionModel.created_at))
            .all()
        )
        return [
            {
                "date": str(d),
                "completion_rate": round((completed / total) * 100, 1) if total else 0.0,
            }
            for d, total, completed in rows
        ]


@router.get("/patient-progress/{patient_id}")
def patient_progress(patient_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            return {"error": "Patient not found"}
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            return {"error": "Patient not found"}

        sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.patient_id == patient_id,
                SessionModel.status == "completed",
                SessionModel.is_deleted == False,
            )
            .order_by(SessionModel.created_at)
            .all()
        )
        return [
            {
                "session_id": s.id,
                "date": s.created_at,
                "exercise_name": s.exercise_name,
                "total_reps": s.total_reps,
                "accuracy": s.final_accuracy,
                "rom": s.final_rom,
                "left_hand_usage_pct": s.left_hand_usage_pct,
                "right_hand_usage_pct": s.right_hand_usage_pct,
                "error_count": s.error_count,
                "avg_reaction_time_ms": s.avg_reaction_time_ms,
            }
            for s in sessions
        ]


@router.get("/game-progress/{patient_id}")
def game_progress(patient_id: str, game_type: str, current_user: User = Depends(get_current_user)):
    """
    Same idea as /patient-progress, but scoped to one game type - e.g. a
    doctor picks a patient AND "Virtual Store" and sees that patient's
    growth on just that game's metrics (arm reach distance, movement
    speed, etc. - whatever services/game_metrics.py's registry defines
    for that game_type) over time, one row per completed session.
    """
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Patient not found")

        fields = get_fields_for(game_type)
        if not fields:
            raise HTTPException(404, f"Unknown game_type '{game_type}'")

        sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.patient_id == patient_id,
                SessionModel.game_type == game_type,
                SessionModel.status == "completed",
                SessionModel.is_deleted == False,
            )
            .order_by(SessionModel.created_at)
            .all()
        )

        return {
            "game_type": game_type,
            "fields": [
                {"key": db_key, "label": label, "format": fmt}
                for db_key, (_vr_field, label, fmt) in fields.items()
            ],
            "sessions": [
                {
                    "session_id": s.id,
                    "date": s.created_at,
                    "exercise_name": s.exercise_name,
                    "metrics": s.game_metrics or {},
                }
                for s in sessions
            ],
        }


@router.get("/exercise-distribution")
def exercise_distribution(patient_id: str | None = None, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        q = (
            db.query(SessionModel.exercise_name, func.count(SessionModel.id))
            .filter(SessionModel.is_deleted == False)
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = q.group_by(SessionModel.exercise_name).all()
        return [{"exercise": name, "count": count} for name, count in rows]


@router.get("/bilateral-hand-usage")
def bilateral_hand_usage(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
                          current_user: User = Depends(get_current_user)):
    """
    Average left vs right hand usage % across sessions in the period.
    Sessions with no hand_side data reported (glove/VR side not sending it
    yet) are simply excluded rather than treated as zero.
    """
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = db.query(SessionModel).filter(
            SessionModel.created_at >= since,
            SessionModel.is_deleted == False,
            SessionModel.left_hand_usage_pct != None,
            SessionModel.right_hand_usage_pct != None,
        )
        q = _scope_to_patient(q, db, current_user, patient_id)

        sessions = q.all()
        if not sessions:
            return {"left_avg_pct": None, "right_avg_pct": None, "sessions_counted": 0}

        left_avg = round(sum(s.left_hand_usage_pct for s in sessions) / len(sessions), 1)
        right_avg = round(sum(s.right_hand_usage_pct for s in sessions) / len(sessions), 1)
        return {"left_avg_pct": left_avg, "right_avg_pct": right_avg, "sessions_counted": len(sessions)}


@router.get("/error-trend")
def error_trend(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
                 current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = (
            db.query(func.date(SessionModel.created_at), func.avg(SessionModel.error_count))
            .filter(
                SessionModel.created_at >= since,
                SessionModel.error_count != None,
                SessionModel.is_deleted == False,
            )
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date(SessionModel.created_at))
            .order_by(func.date(SessionModel.created_at))
            .all()
        )
        return [{"date": str(d), "avg_errors": round(e, 1)} for d, e in rows]


@router.get("/reaction-time-trend")
def reaction_time_trend(days: int = Query(30, ge=1, le=365), patient_id: str | None = None,
                         current_user: User = Depends(get_current_user)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        q = (
            db.query(func.date(SessionModel.created_at), func.avg(SessionModel.avg_reaction_time_ms))
            .filter(
                SessionModel.created_at >= since,
                SessionModel.avg_reaction_time_ms != None,
                SessionModel.is_deleted == False,
            )
        )
        q = _scope_to_patient(q, db, current_user, patient_id)
        rows = (
            q.group_by(func.date(SessionModel.created_at))
            .order_by(func.date(SessionModel.created_at))
            .all()
        )
        return [{"date": str(d), "avg_reaction_time_ms": round(r, 1)} for d, r in rows]


@router.get("/program-completion/{patient_id}")
def program_completion(patient_id: str, current_user: User = Depends(get_current_user)):
    """
    % of a patient's planned treatment sessions completed so far.
    Requires Patient.planned_total_sessions to be set; otherwise returns
    null so the frontend can show "not set" instead of a misleading 0%.
    """
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Patient not found")

        completed_sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.patient_id == patient_id,
                SessionModel.status == "completed",
                SessionModel.is_deleted == False,
            )
            .count()
        )

        if not patient.planned_total_sessions:
            return {
                "completed_sessions": completed_sessions,
                "planned_total_sessions": None,
                "completion_pct": None,
            }

        completion_pct = round((completed_sessions / patient.planned_total_sessions) * 100, 1)
        return {
            "completed_sessions": completed_sessions,
            "planned_total_sessions": patient.planned_total_sessions,
            "completion_pct": min(completion_pct, 100.0),
        }


@router.get("/device-telemetry-summary")
def device_telemetry_summary(days: int = Query(7, ge=1, le=90), current_user: User = Depends(get_current_user)):
    """
    Rolling technical-health summary (avg FPS, load time, latency) from
    VR headset telemetry. Empty result just means no telemetry has been
    reported yet, not that the platform is unhealthy.
    """
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        row = (
            db.query(
                func.avg(DeviceTelemetry.fps),
                func.avg(DeviceTelemetry.load_time_ms),
                func.avg(DeviceTelemetry.latency_ms),
                func.count(DeviceTelemetry.id),
            )
            .filter(DeviceTelemetry.recorded_at >= since)
            .first()
        )
        avg_fps, avg_load, avg_latency, count = row
        if not count:
            return {"avg_fps": None, "avg_load_time_ms": None, "avg_latency_ms": None, "samples": 0}

        return {
            "avg_fps": round(avg_fps, 1) if avg_fps else None,
            "avg_load_time_ms": round(avg_load, 1) if avg_load else None,
            "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
            "samples": count,
        }
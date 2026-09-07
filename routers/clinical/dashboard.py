import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func

from database import get_db
from models import SessionModel, Patient, ActivityLog, Report, DeviceTelemetry, User
from services.vr_ws_manager import vr_manager, dashboard_manager
from services.game_metrics import registry_as_json
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/game-metrics-schema")
def game_metrics_schema(current_user: User = Depends(get_current_user)):
    """Per-game metric field definitions (services/game_metrics.py) as
    JSON, so session.html can render whichever game's live metrics
    generically instead of needing a code change for every new game."""
    return registry_as_json()


def _owned_patient_ids(db, current_user: User) -> list[str]:
    return [p.id for p in db.query(Patient.id).filter(Patient.responsible_therapist_id == current_user.id)]


@router.get("/overview")
def overview(patient_id: str | None = None, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        # A therapist explicitly asking about a patient_id must own it;
        # with no patient_id, all the org-wide counts below get scoped
        # down to just this therapist's own patients/sessions.
        owned_ids: list[str] | None = None
        if patient_id:
            patient = db.query(Patient).filter(Patient.id == patient_id).first()
            if not patient:
                raise HTTPException(404, "Patient not found")
            if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
                raise HTTPException(404, "Patient not found")
        elif current_user.role == "therapist":
            owned_ids = _owned_patient_ids(db, current_user)

        today = datetime.datetime.utcnow().date()
        today_start = datetime.datetime.combine(today, datetime.time.min)
        today_end = datetime.datetime.combine(today, datetime.time.max)

        def _scope(q, patient_col):
            """Applies the patient_id filter if one was given (already
            ownership-checked above), otherwise restricts to this
            therapist's own patients when owned_ids is set."""
            if patient_id:
                return q.filter(patient_col == patient_id)
            if owned_ids is not None:
                return q.filter(patient_col.in_(owned_ids))
            return q

        active_session_ids = list(vr_manager.active_sessions.keys())
        if owned_ids is not None:
            # Only count this therapist's own live sessions as "active".
            own_live_ids = {
                sid for sid, pid in db.query(SessionModel.id, SessionModel.patient_id)
                .filter(SessionModel.id.in_(active_session_ids)).all()
                if pid in owned_ids
            } if active_session_ids else set()
            active_session_ids = list(own_live_ids)
        active_sessions = len(active_session_ids)

        connected_patients = db.query(SessionModel.patient_id).filter(
            SessionModel.id.in_(active_session_ids)
        ).distinct().count() if active_session_ids else 0

        todays_q = db.query(SessionModel).filter(
            SessionModel.created_at >= today_start,
            SessionModel.created_at <= today_end,
        )
        todays_q = _scope(todays_q, SessionModel.patient_id)
        todays_sessions = todays_q.count()

        upcoming_q = db.query(SessionModel).filter(SessionModel.status == "pending")
        upcoming_q = _scope(upcoming_q, SessionModel.patient_id)
        upcoming_sessions = upcoming_q.count()

        total_q = db.query(SessionModel).filter(SessionModel.is_deleted == False)
        total_q = _scope(total_q, SessionModel.patient_id)
        total_sessions = total_q.count()

        completed_q = db.query(SessionModel).filter(SessionModel.status == "completed")
        completed_q = _scope(completed_q, SessionModel.patient_id)
        completed_sessions = completed_q.count()

        completion_rate = round((completed_sessions / total_sessions) * 100, 1) if total_sessions else 0.0

        avg_duration_q = db.query(func.avg(SessionModel.duration_seconds)).filter(
            SessionModel.duration_seconds != None
        )
        avg_duration_q = _scope(avg_duration_q, SessionModel.patient_id)
        avg_duration = avg_duration_q.scalar()

        reports_q = db.query(Report)
        reports_q = _scope(reports_q, Report.patient_id)
        reports_generated = reports_q.count()

        # Bilateral hand usage — last 30 days, scoped to patient_id if given.
        since_30d = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        hand_q = db.query(SessionModel).filter(
            SessionModel.created_at >= since_30d,
            SessionModel.left_hand_usage_pct != None,
            SessionModel.right_hand_usage_pct != None,
        )
        hand_q = _scope(hand_q, SessionModel.patient_id)
        hand_rows = hand_q.all()
        left_hand_avg = round(sum(s.left_hand_usage_pct for s in hand_rows) / len(hand_rows), 1) if hand_rows else None
        right_hand_avg = round(sum(s.right_hand_usage_pct for s in hand_rows) / len(hand_rows), 1) if hand_rows else None

        # Avg errors / reaction time — last 30 days, scoped to patient_id if given.
        error_q = db.query(func.avg(SessionModel.error_count)).filter(
            SessionModel.created_at >= since_30d,
            SessionModel.error_count != None,
        )
        error_q = _scope(error_q, SessionModel.patient_id)
        error_avg = error_q.scalar()

        reaction_q = db.query(func.avg(SessionModel.avg_reaction_time_ms)).filter(
            SessionModel.created_at >= since_30d,
            SessionModel.avg_reaction_time_ms != None,
        )
        reaction_q = _scope(reaction_q, SessionModel.patient_id)
        reaction_avg = reaction_q.scalar()

        # Device/platform technical health — last 7 days. This reflects
        # headset/network health, not any one patient's data, so it stays
        # org-wide even when patient_id is given.
        since_7d = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        telemetry_row = db.query(
            func.avg(DeviceTelemetry.fps),
            func.avg(DeviceTelemetry.latency_ms),
        ).filter(DeviceTelemetry.recorded_at >= since_7d).first()
        avg_fps, avg_latency = telemetry_row if telemetry_row else (None, None)

        total_patients_q = db.query(Patient).filter(Patient.is_active == True)
        if owned_ids is not None:
            total_patients_q = total_patients_q.filter(Patient.responsible_therapist_id == current_user.id)

        return {
            "active_sessions": active_sessions,
            "connected_vr_devices": active_sessions,
            "connected_patients": connected_patients,
            "connected_dashboards": len(dashboard_manager.connections),
            "todays_sessions": todays_sessions,
            "upcoming_sessions": upcoming_sessions,
            "completion_rate": completion_rate,
            "avg_session_duration_seconds": round(avg_duration, 1) if avg_duration else 0,
            "reports_generated": reports_generated,
            "total_patients": total_patients_q.count(),

            # New: bilateral / error / reaction summary (null if no data yet)
            "left_hand_usage_avg_pct": left_hand_avg,
            "right_hand_usage_avg_pct": right_hand_avg,
            "avg_error_count": round(error_avg, 1) if error_avg else None,
            "avg_reaction_time_ms": round(reaction_avg, 1) if reaction_avg else None,

            # New: device technical health snapshot (null if no telemetry yet)
            "avg_fps": round(avg_fps, 1) if avg_fps else None,
            "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
        }


@router.get("/recent-activity")
def recent_activity(limit: int = 15, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        q = db.query(ActivityLog).order_by(ActivityLog.timestamp.desc())

        if current_user.role == "therapist":
            # Every activity entry the app currently writes is
            # entity_type="Patient" (create/update/deactivate). A
            # therapist should only ever see activity for their OWN
            # patients — so filter directly at the DB level, and if any
            # other entity_type ever gets logged in future, it's
            # excluded by default for a therapist rather than shown to
            # everyone (safer default than an allow-list mistake).
            owned_ids = _owned_patient_ids(db, current_user)
            q = q.filter(ActivityLog.entity_type == "Patient", ActivityLog.entity_id.in_(owned_ids))

        logs = q.limit(limit).all()

        return [
            {
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "timestamp": log.timestamp,
            }
            for log in logs
        ]
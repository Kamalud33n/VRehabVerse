from fastapi import APIRouter, HTTPException, Depends

from database import get_db
from models import SessionModel, Patient, User
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/sessions", tags=["export"])

SCHEMA_VERSION = "1.1"


@router.get("/{session_id}/export")
async def export_session_report(session_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        if current_user.role == "therapist" and (not patient or patient.responsible_therapist_id != current_user.id):
            raise HTTPException(status_code=404, detail="Session not found")

        total_reps = session.total_reps or 0
        avg_time_per_rep = (
            round(session.duration_seconds / total_reps, 2)
            if session.duration_seconds and total_reps
            else 0.0
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "report_type": "vr_rehab_session",
            "source_system": "mednova-vr",
            "session": {
                "session_id": session.id,
                "patient_id": session.patient_id,
                "doctor_name": session.doctor_name,
                "therapist_name": session.therapist_name,
                "status": session.status,
                "started_at": session.start_time.isoformat() if session.start_time else None,
                "ended_at": session.end_time.isoformat() if session.end_time else None,
                "exercise_name": session.exercise_name,
            },
            "metrics": {
                "total_reps": total_reps,
                "accuracy_pct": round(session.final_accuracy or 0.0, 1),
                "rom_deg": round(session.final_rom or 0.0, 1),
                "duration_seconds": session.duration_seconds or 0,
                "avg_time_per_rep_sec": avg_time_per_rep,
            },
            # New in schema v1.1 — bilateral hand usage, error count, and
            # reaction time. Any field is null when the source session
            # predates this feature or the VR/glove device didn't report it.
            "extended_metrics": {
                "left_hand_usage_pct": session.left_hand_usage_pct,
                "right_hand_usage_pct": session.right_hand_usage_pct,
                "error_count": session.error_count,
                "avg_reaction_time_ms": session.avg_reaction_time_ms,
            },
        }
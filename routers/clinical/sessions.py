import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from database import get_db
from models import SessionModel, SessionCode, Patient, Setting, User, AuditLog, generate_session_code
from services.vr_ws_manager import vr_manager, dashboard_manager
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Whitelist for ?sort_by= - same reasoning as PATIENT_SORT_FIELDS in
# patients.py: getattr(SessionModel, sort_by, ...) previously accepted any
# string, which could 500 on a non-column name or expose internal
# attribute/column names via trial and error.
SESSION_SORT_FIELDS = {"created_at", "updated_at", "status", "start_time", "end_time", "exercise_name"}


class SessionCreate(BaseModel):
    patient_id: str
    doctor_name: str | None = None
    therapist_name: str | None = None
    exercise_name: str


class SessionOut(BaseModel):
    id: str
    patient_id: str
    doctor_name: str | None
    therapist_name: str | None
    supervising_user_id: str | None = None
    exercise_name: str
    status: str
    end_reason: str | None = None
    start_time: datetime.datetime | None
    end_time: datetime.datetime | None
    duration_seconds: int | None
    total_reps: int | None
    final_accuracy: float | None
    final_rom: float | None

    # VR session-summary fields (from the standalone VR app's end-of-game result)
    game_name: str | None = None
    level_name: str | None = None
    score: int | None = None
    completion_percentage: float | None = None
    attempts_count: int | None = None
    error_count: int | None = None
    left_hand_usage_pct: float | None = None
    right_hand_usage_pct: float | None = None
    response_time_sec: float | None = None
    max_hand_raise_height: float | None = None

    # Level 1 (Warm Up) frozen snapshot - was stored on the model but
    # never surfaced here, so session.html's Level 1 block (and
    # report_builder's level1-preferred Max Hand Raise) always saw
    # these as undefined/null from this endpoint.
    level1_score: int | None = None
    level1_completion_percentage: float | None = None
    level1_error_count: int | None = None
    level1_attempts_count: int | None = None
    level1_response_time_sec: float | None = None
    level1_right_hand_usage_pct: float | None = None
    level1_left_hand_usage_pct: float | None = None
    level1_max_hand_raise_height: float | None = None

    # Per-game metric group (services/game_metrics.py) - e.g. Virtual
    # Store's arm-reach/movement fields for Level 2.
    game_type: str | None = None
    game_metrics: dict | None = None

    class Config:
        from_attributes = True


def _code_ttl_minutes(db) -> int:
    setting = db.query(Setting).filter(Setting.key == "session_code_ttl_minutes").first()
    return int(setting.value) if setting else 15


def _get_owned_session(db, session_id: str, current_user: User) -> SessionModel:
    """Loads a session and enforces that a therapist can only reach
    sessions belonging to their own patients. Super admins can reach any
    session. Returns 404 (not 403) on mismatch so a therapist can't probe
    for the existence of another therapist's session/patient."""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    if current_user.role == "therapist":
        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        if not patient or patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Session not found")
    return session


class TestMetricsUpdate(BaseModel):
    """
    TEST-ONLY payload for manually setting the legacy accuracy/ROM fields
    that report_builder.py reads (final_accuracy, final_rom). The current
    VR session-summary flow (see routers/ws.py) does not populate these —
    it only sets score/completion_percentage. Use this endpoint to test
    report generation with specific accuracy/ROM values until/unless the
    VR app is updated to report these directly.
    """
    final_accuracy: float | None = None
    final_rom: float | None = None
    total_reps: int | None = None


@router.post("", response_model=SessionOut)
def create_session(payload: SessionCreate, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Patient not found")

        session = SessionModel(
            patient_id=payload.patient_id,
            # Supervising specialist is identified from the authenticated
            # session, never re-entered manually. therapist_name is kept
            # as a free-text snapshot of the logged-in user's name (so
            # report_builder/exports that read it don't need to change),
            # but the source of truth is supervising_user_id.
            doctor_name=payload.doctor_name or patient.doctor_name,
            therapist_name=current_user.full_name,
            supervising_user_id=current_user.id,
            exercise_name=payload.exercise_name,
            status="pending",
        )
        db.add(session)
        db.flush()

        code = SessionCode(
            session_id=session.id,
            code=generate_session_code(),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=_code_ttl_minutes(db)),
        )
        db.add(code)

        db.add(AuditLog(entity_type="Session", entity_id=session.id, action="session_created",
                         performed_by=current_user.id,
                         details={"patient_id": patient.id, "exercise_name": payload.exercise_name}))

        db.commit()
        db.refresh(session)
        return session


@router.get("")
def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    patient_id: str | None = None,
    search: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    current_user: User = Depends(get_current_user),
):
    with get_db() as db:
        q = db.query(SessionModel).filter(SessionModel.is_deleted == False)

        # Therapists only ever see sessions belonging to their own patients.
        if current_user.role == "therapist":
            q = q.join(Patient, Patient.id == SessionModel.patient_id).filter(
                Patient.responsible_therapist_id == current_user.id
            )

        if status:
            q = q.filter(SessionModel.status == status)
        if patient_id:
            q = q.filter(SessionModel.patient_id == patient_id)
        if search:
            q = q.filter(SessionModel.exercise_name.ilike(f"%{search}%"))

        sort_by = sort_by if sort_by in SESSION_SORT_FIELDS else "created_at"
        sort_col = getattr(SessionModel, sort_by)
        q = q.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [SessionOut.model_validate(i) for i in items],
        }


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)
        return session


@router.get("/{session_id}/code")
def get_session_code(session_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        _get_owned_session(db, session_id, current_user)
        code = db.query(SessionCode).filter(SessionCode.session_id == session_id).first()
        if not code:
            raise HTTPException(404, "Session code not found")
        return {
            "code": code.code,
            "is_used": code.is_used,
            "expires_at": code.expires_at,
        }


@router.post("/{session_id}/regenerate-code")
def regenerate_code(session_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)

        old_code = db.query(SessionCode).filter(SessionCode.session_id == session_id).first()
        if old_code:
            db.delete(old_code)
            db.flush()

        new_code = SessionCode(
            session_id=session_id,
            code=generate_session_code(),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=_code_ttl_minutes(db)),
        )
        db.add(new_code)
        db.commit()
        return {"code": new_code.code, "expires_at": new_code.expires_at}


@router.post("/{session_id}/start-training")
async def start_training(session_id: str, current_user: User = Depends(get_current_user)):
    """
    Called from the dashboard once the doctor/therapist has entered the
    session code on the headset AND handed it to the patient and is ready
    to begin. The headset connecting (session-code handshake) only pairs
    it to this session — it does NOT start the game or the timer. This
    endpoint is what actually tells the VR app to begin, so timing starts
    when the patient really starts, not while the doctor is still typing
    the code / getting the headset onto the patient.
    """
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)

        if not vr_manager.is_connected(session_id):
            raise HTTPException(409, "VR headset is not connected for this session yet")

        session.status = "live"
        session.start_time = session.start_time or datetime.datetime.utcnow()
        db.commit()

    sent = await vr_manager.send_to_session(session_id, {"command": "start_training"})
    if not sent:
        # The connection looked alive in is_connected() a moment ago, but
        # send failed/timed out - it was actually stale. Fail clearly
        # instead of telling the dashboard "training_started" when the
        # headset never actually got the command.
        raise HTTPException(409, "VR headset connection is unresponsive - ask the patient to reconnect")

    await dashboard_manager.broadcast_global({"type": "training_started", "session_id": session_id})

    return {"status": "training_started"}


SESSION_END_REASONS = {"manual", "emergency"}


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    reason: str = Query("manual", description="'manual' (End Session) or 'emergency' (Emergency Stop)"),
    current_user: User = Depends(get_current_user),
):
    if reason not in SESSION_END_REASONS:
        raise HTTPException(422, f"reason must be one of {sorted(SESSION_END_REASONS)}")

    # Verify ownership and record why the dashboard requested the stop.
    # Do NOT mark the session completed yet when VR is connected: Unity must
    # receive the command, stop the game, calculate the current metrics, and
    # send its session_end payload first.
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)
        session.end_reason = reason

        db.add(AuditLog(
            entity_type="Session",
            entity_id=session.id,
            action="session_ended_emergency" if reason == "emergency" else "session_ended_manual",
            performed_by=current_user.id,
            details={"reason": reason},
        ))
        db.commit()

    # Stop the connected headset now. Unity will send the current
    # GameSessionData as the durable session_end payload.
    sent = await vr_manager.send_to_session(
        session_id,
        {"command": "end_session" if reason == "manual" else "emergency_stop"},
    )

    if sent:
        # ws.py changes the status to completed only after the VR summary is
        # received and saved.
        return {"status": "ending", "end_reason": reason}

    # No responsive VR connection means there is no current VR summary to
    # save. Complete the dashboard-side session as a fallback.
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)
        session.status = "completed"
        session.end_time = datetime.datetime.utcnow()
        session.end_reason = reason
        db.commit()

    await dashboard_manager.broadcast_global({
        "type": "session_ended",
        "session_id": session_id,
        "reason": reason,
    })

    return {
        "status": "completed",
        "end_reason": reason,
        "vr_connected": False,
    }


class SessionTestData(BaseModel):
    """
    TEST-ONLY. Lets you set the fields that normally only arrive via the
    real VR websocket flow (/ws/vr), so the full patient -> session ->
    report flow can be tested end-to-end in Postman without a real VR
    headset connecting. Not meant to be used by the actual VR app.
    """
    final_accuracy: float | None = None
    final_rom: float | None = None
    score: int | None = None
    completion_percentage: float | None = None
    error_count: int | None = None
    attempts_count: int | None = None
    total_reps: int | None = None
    left_hand_usage_pct: float | None = None
    right_hand_usage_pct: float | None = None
    avg_reaction_time_ms: float | None = None


@router.patch("/{session_id}/test-data", response_model=SessionOut)
def set_test_data(session_id: str, payload: SessionTestData, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)

        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(session, field, value)

        db.commit()
        db.refresh(session)
        return session


@router.delete("/{session_id}")
def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        session = _get_owned_session(db, session_id, current_user)

        session.is_deleted = True
        session.deleted_at = datetime.datetime.utcnow()
        db.commit()
        return {"status": "deleted"}
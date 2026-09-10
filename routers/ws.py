import asyncio
import datetime
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from database import get_db
from models import SessionModel, SessionCode, LiveMetric, Patient, User
from services.vr_ws_manager import vr_manager, dashboard_manager
from services.game_metrics import detect_game_type, extract_game_metrics
from auth.security import decode_access_token, COOKIE_NAME

router = APIRouter(tags=["websocket"])

# A dropped VR socket (network hiccup, Cloudflare tunnel blip, the app
# briefly stalling during a level transition) raises the exact same
# WebSocketDisconnect as a genuine "the patient took the headset off and
# walked away" end-of-game. Previously both were treated identically -
# any disconnect immediately marked the session "completed", so a
# transient drop mid-game silently killed the session even though the
# headset kept playing. Instead, on disconnect we wait this long for the
# VR app to reconnect (same session_code - see the handshake check
# below, which now allows reusing an already-used code as long as the
# session hasn't been finalized yet) before actually finalizing.
RECONNECT_GRACE_SECONDS = 20

# session_id -> the pending "finalize if nobody reconnected" task, so a
# handshake that reconnects in time can cancel it before it fires.
_pending_disconnect_finalize: dict[str, asyncio.Task] = {}

# Hard cap on a single base64 vr_frame payload. A raw JPEG this size is
# already generous for a headset POV thumbnail - anything bigger gets
# dropped rather than relayed, so one oversized/corrupt frame can't bloat
# memory or stall a broadcast. Backend-only guard, VR app unaffected as
# long as it's sending reasonably-sized frames (which it already does).
MAX_FRAME_B64_CHARS = 250_000  # ~180KB decoded


# ---------------------------------------------------------------------------
# Legacy live-streaming payload (per-rep updates). Kept for any device that
# still streams live metrics. The standalone VR app does NOT use this —
# it uses the session-code handshake + VRSessionSummary flow below instead.
# ---------------------------------------------------------------------------
class VRPayload(BaseModel):
    session_code: str
    patient_id: str
    exercise: str
    rep_count: int
    accuracy: float
    rom: float
    status: str
    elapsed_time: int
    timestamp: str | None = None

    hand_side: str | None = None          # "left" | "right" | "both"
    reaction_time_ms: float | None = None
    is_error: bool = False


# ---------------------------------------------------------------------------
# First message the VR app sends right after connecting — just the code
# the therapist/patient typed in on the headset.
# ---------------------------------------------------------------------------
class SessionCodeHandshake(BaseModel):
    session_code: str


# ---------------------------------------------------------------------------
# Final message the VR app sends once the game/session ends. Field names
# match the VR app's GameSessionData class exactly (PascalCase), so no
# changes are needed on the VR side to fit this shape.
# ---------------------------------------------------------------------------
class VRSessionSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(alias="PatientID")
    session_id: str = Field(alias="SessionID")
    therapist_id: str | None = Field(default=None, alias="TherapistID")

    game_name: str | None = Field(default=None, alias="GameName")
    level_name: str | None = Field(default=None, alias="LevelName")

    start_time: str | None = Field(default=None, alias="StartTime")
    end_time: str | None = Field(default=None, alias="EndTime")
    session_duration: float | None = Field(default=None, alias="SessionDuration")

    completion_percentage: float | None = Field(default=None, alias="CompletionPercentage")

    errors_count: int | None = Field(default=None, alias="ErrorsCount")
    attempts_count: int | None = Field(default=None, alias="AttemptsCount")

    right_hand_usage: float | None = Field(default=None, alias="RightHandUsage")
    left_hand_usage: float | None = Field(default=None, alias="LeftHandUsage")

    response_time: float | None = Field(default=None, alias="ResponseTime")

    score: int | None = Field(default=None, alias="Score")
    max_hand_raise_height: float | None = Field(default=None, alias="MaxHandRaiseHeight")

    # Level 1 (Warm Up) frozen snapshot — sent alongside the fields above
    # (which represent Level 2 / Virtual Store) once the VR app supports
    # the two-level Warm Up -> Virtual Store flow. Absent/None on older
    # VR builds or single-level sessions.
    level_1_score: int | None = Field(default=None, alias="Level1Score")
    level_1_completion_percentage: float | None = Field(default=None, alias="Level1CompletionPercentage")
    level_1_errors_count: int | None = Field(default=None, alias="Level1ErrorsCount")
    level_1_attempts_count: int | None = Field(default=None, alias="Level1AttemptsCount")
    level_1_response_time: float | None = Field(default=None, alias="Level1ResponseTime")
    level_1_right_hand_usage: float | None = Field(default=None, alias="Level1RightHandUsage")
    level_1_left_hand_usage: float | None = Field(default=None, alias="Level1LeftHandUsage")
    level_1_max_hand_raise_height: float | None = Field(default=None, alias="Level1MaxHandRaiseHeight")


def _parse_vr_datetime(value: str | None):
    """VR app sends 'yyyy-MM-dd HH:mm:ss' strings — parse defensively."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


@router.websocket("/ws/vr")
async def ws_vr(websocket: WebSocket):
    """
    VR headset connection flow:
      1. VR app connects and sends ONE handshake message: {"session_code": "..."}
      2. We validate the code against the session the therapist created on
         the dashboard (patient + game already chosen there — the VR app
         does not select these itself).
      3. We reply with the authoritative patient_id / game_name / session_id
         so the VR app doesn't need to rely on anything hardcoded locally.
      4. The patient plays the game. Throughout, the VR app pushes
         {"type":"live_update",...} metric snapshots and
         {"type":"vr_frame",...} headset POV frames - both are relayed
         straight to the dashboard, not persisted. For multi-level games,
         it also sends {"type":"level_complete","level_name":...} when a
         level finishes - this only relays a "level_changed" UI event and
         otherwise does NOT affect the session/stream/socket in any way.
      5. When the ENTIRE game (all levels) ends, the VR app sends
         {"type":"session_end",...} matching its GameSessionData JSON.
         Only this message saves the session and closes the connection -
         level_complete must never be confused with it.
    """
    await websocket.accept()
    session_id: str | None = None

    try:
        # --- Step 1: handshake (session code) ---
        raw = await websocket.receive_json()
        try:
            handshake = SessionCodeHandshake(**raw)
        except ValidationError:
            await websocket.send_json({"error": "invalid_payload"})
            await websocket.close()
            return

        with get_db() as db:
            # NOTE: no longer filtering on SessionCode.is_used == False here.
            # A code is still looked up by value + expiry only - whether this
            # is a first connect or a reconnect after a dropped socket is
            # decided below from the SESSION's status, not the code's
            # is_used flag (which stays a simple "has this code ever been
            # used" marker for the dashboard's code panel).
            code_row = (
                db.query(SessionCode)
                .filter(SessionCode.code == handshake.session_code)
                .first()
            )

            if not code_row or code_row.expires_at < datetime.datetime.utcnow():
                await websocket.send_json({"error": "invalid_or_expired_code"})
                await websocket.close()
                return

            session = db.query(SessionModel).filter(SessionModel.id == code_row.session_id).first()
            if not session:
                await websocket.send_json({"error": "session_not_found"})
                await websocket.close()
                return

            # A "completed" session (real end-of-game, or the reconnect
            # grace window already expired) is genuinely over - the code
            # should not resurrect it. "pending" (first connect) and "live"
            # (fresh connect or reconnect within the grace window) are both
            # fine to attach to.
            if session.status == "completed":
                await websocket.send_json({"error": "invalid_or_expired_code"})
                await websocket.close()
                return

            session_id = session.id

            # If a disconnect from THIS session is currently sitting in its
            # grace window waiting to finalize, this handshake is the
            # reconnect it was waiting for - cancel the finalize so it
            # doesn't mark the session "completed" out from under us.
            pending_finalize = _pending_disconnect_finalize.pop(session_id, None)
            if pending_finalize and not pending_finalize.done():
                pending_finalize.cancel()

            vr_manager.active_sessions[session_id] = websocket
            code_row.is_used = True
            code_row.used_at = datetime.datetime.utcnow()
            session.status = "live"
            session.start_time = session.start_time or datetime.datetime.utcnow()
            db.commit()

            # Authoritative values come from OUR db, not from the VR app —
            # the VR app should use these rather than anything hardcoded locally.
            await websocket.send_json({
                "status": "connected",
                "session_id": session.id,
                "patient_id": session.patient_id,
                "game_name": session.exercise_name,
            })

            # Lifecycle event - every connected dashboard tab wants this
            # (list page refreshes, session page updates its status dot).
            await dashboard_manager.broadcast_global({
                "type": "session_connected",
                "session_id": session.id,
            })

        # --- Step 2: live session loop ---
        # The VR app now stays connected for the whole session, pushing
        # frequent small messages instead of just one at the end:
        #   {"type":"live_update", ...GameSessionData}  - per-metric-change + heartbeat
        #   {"type":"vr_frame","frame":"<base64 jpg>"}  - headset POV frame
        #   {"type":"level_complete", "level_name": "...", ...}  - one level
        #        of a multi-level game finished. Session/stream/socket
        #        stay alive - the game continues into the next level.
        #        NOT the same as session_end. See note below.
        #   {"type":"session_end", ...GameSessionData}  - final summary,
        #        sent ONLY when the entire game (all levels) is over -
        #        this is what ends the loop and tears down the session.
        # live_update / vr_frame / level_complete are relayed straight to
        # the dashboard and NOT persisted to the DB here - they're cheap,
        # frequent, and disposable. The durable write only happens once,
        # below, on session_end.
        #
        # IMPORTANT: level_complete must NEVER be treated like session_end.
        # A level finishing is not the session finishing - only an
        # explicit session_end from the VR app should mark the session
        # "completed" and close the socket. This is what keeps the live
        # stream/session page alive across level transitions.
        raw: dict = {}
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type")

            if msg_type == "level_complete":
                # Level boundary only - explicitly does NOT touch
                # session.status, does NOT call vr_manager.disconnect(),
                # does NOT call dashboard_manager.clear_session(), and
                # does NOT close the websocket. The session/stream stay
                # fully alive; only a lightweight UI signal goes out so
                # session.html can show which level is now active instead
                # of flipping to an "ended" state.
                await dashboard_manager.broadcast_to_session(session_id, {
                    "type": "level_changed",
                    "session_id": session_id,
                    "level_name": raw.get("level_name") or raw.get("LevelName"),
                })
                continue

            if msg_type == "vr_frame":
                frame = raw.get("frame")
                # Drop oversized frames rather than relay them - one bad
                # frame shouldn't be able to bloat memory or slow down
                # every dashboard watching this session.
                if frame and len(frame) > MAX_FRAME_B64_CHARS:
                    continue
                # Server-side throttle (~3fps) regardless of how fast the
                # headset is actually sending - only sent to dashboards
                # watching THIS session, not every open tab.
                if dashboard_manager.should_send_frame(session_id):
                    await dashboard_manager.broadcast_to_session(session_id, {
                        "type": "vr_frame",
                        "session_id": session_id,
                        "frame": frame,
                    })
                continue

            if msg_type == "live_update":
                payload = dict(raw)
                payload["type"] = "live_update"
                payload["session_id"] = session_id
                # Only to dashboards watching this specific session.
                await dashboard_manager.broadcast_to_session(session_id, payload)
                continue

            if msg_type == "session_end":
                break

            # Unknown/legacy shape - ignore rather than crash the socket
            # on a message type we don't understand yet.

        # --- Step 3: final session summary ---
        try:
            summary = VRSessionSummary(**raw)
        except ValidationError as e:
            await websocket.send_json({"error": "invalid_summary_payload", "detail": str(e)})
            await websocket.close()
            return

        with get_db() as db:
            session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not session:
                await websocket.send_json({"error": "session_not_found"})
                await websocket.close()
                return

            session.game_name = summary.game_name
            session.level_name = summary.level_name

            parsed_start = _parse_vr_datetime(summary.start_time)
            parsed_end = _parse_vr_datetime(summary.end_time)
            if parsed_start:
                session.start_time = parsed_start
            if parsed_end:
                session.end_time = parsed_end

            if summary.session_duration is not None:
                session.duration_seconds = int(summary.session_duration)

            session.completion_percentage = summary.completion_percentage
            session.error_count = summary.errors_count
            session.attempts_count = summary.attempts_count
            session.right_hand_usage_pct = summary.right_hand_usage
            session.left_hand_usage_pct = summary.left_hand_usage
            session.response_time_sec = summary.response_time
            session.score = summary.score
            session.max_hand_raise_height = summary.max_hand_raise_height

            # Level 1 (Warm Up) snapshot — kept separate from the Level 2
            # fields above so neither level overwrites the other.
            session.level1_score = summary.level_1_score
            session.level1_completion_percentage = summary.level_1_completion_percentage
            session.level1_error_count = summary.level_1_errors_count
            session.level1_attempts_count = summary.level_1_attempts_count
            session.level1_response_time_sec = summary.level_1_response_time
            session.level1_right_hand_usage_pct = summary.level_1_right_hand_usage
            session.level1_left_hand_usage_pct = summary.level_1_left_hand_usage
            session.level1_max_hand_raise_height = summary.level_1_max_hand_raise_height

            # Per-game metric group (see services/game_metrics.py). Detected
            # from whichever names this session actually has — the VR app's
            # own game_name/level_name take priority, exercise_name (the
            # dashboard-selected game at session creation) is the fallback
            # for VR builds that don't report game_name/level_name at all.
            session.game_type = detect_game_type(
                summary.game_name, summary.level_name, session.exercise_name
            )
            # `raw` is the original session_end JSON as sent by the VR app
            # (PascalCase field names) — game_metrics pulls straight from
            # it since those fields aren't declared on VRSessionSummary.
            session.game_metrics = extract_game_metrics(session.game_type, raw)

            # report_builder.py reads final_accuracy/total_reps (legacy
            # webcam-pose-tracking fields) for the score gauge, KPI cards,
            # progress chart, and session history table. The VR app's
            # end-of-game summary doesn't have a direct "accuracy" or
            # "rep count" concept, so we derive the closest equivalents
            # here instead of touching every consumer of those fields:
            #   - completion_percentage is already a 0-100 score, same
            #     scale report_builder expects for "accuracy"
            #   - attempts_count is the closest analogue to "total reps"
            if summary.completion_percentage is not None:
                session.final_accuracy = summary.completion_percentage
            if summary.attempts_count is not None:
                session.total_reps = summary.attempts_count

            session.status = "completed"
            session.end_time = session.end_time or datetime.datetime.utcnow()

            db.commit()

            await websocket.send_json({"status": "saved", "session_id": session.id})

            await dashboard_manager.broadcast_global({
                "type": "session_ended",
                "session_id": session.id,
                "score": summary.score,
                "completion_percentage": summary.completion_percentage,
                "reason": session.end_reason,
            })

        # Bug fix: this was previously only cleaned up in the
        # WebSocketDisconnect except-block below, so a session that
        # finished normally (summary saved, server closes the socket
        # itself) stayed stuck in active_sessions forever — dashboard
        # kept showing it as "connected" even after it was done.
        vr_manager.disconnect(session_id)
        dashboard_manager.clear_session(session_id)
        await websocket.close()

    except WebSocketDisconnect:
        if session_id:
            vr_manager.disconnect(session_id)
            dashboard_manager.clear_session(session_id)
            # Don't finalize immediately - this same exception fires for a
            # transient drop mid-game just as much as a real end-of-game,
            # and we can't tell them apart at the transport level. Give the
            # VR app RECONNECT_GRACE_SECONDS to reconnect (handshake above
            # now allows re-attaching to a "live" session) before treating
            # this as the game actually being over.
            old = _pending_disconnect_finalize.get(session_id)
            if old and not old.done():
                old.cancel()
            _pending_disconnect_finalize[session_id] = asyncio.create_task(
                _finalize_if_not_reconnected(session_id)
            )


async def _finalize_if_not_reconnected(session_id: str):
    """Runs RECONNECT_GRACE_SECONDS after a drop; cancelled early if the
    VR app reconnects in time (see the handshake step above). If it runs
    to completion, no reconnect happened - only then do we treat the
    disconnect as a genuine end-of-game."""
    try:
        await asyncio.sleep(RECONNECT_GRACE_SECONDS)
    except asyncio.CancelledError:
        return

    with get_db() as db:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if session and session.status == "live":
            session.status = "completed"
            session.end_time = datetime.datetime.utcnow()
            db.commit()
            await dashboard_manager.broadcast_global({
                "type": "session_ended",
                "session_id": session_id,
            })
    _pending_disconnect_finalize.pop(session_id, None)


async def _authenticate_dashboard_ws(websocket: WebSocket) -> User | None:
    """
    Same cookie-first / bearer-header-fallback token lookup as
    auth/dependencies.py's HTTP dependency, adapted for a WebSocket
    (Starlette's WebSocket exposes .cookies / .headers the same way
    Request does). Returns None on any missing/invalid/expired token,
    inactive/unverified/unapproved account, or deleted user - callers
    must close the socket without accepting in that case so the client
    gets a clean rejection instead of silently receiving live session
    data (patient metrics, headset POV frames) with no auth check.
    """
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None

    with get_db() as db:
        user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()
        if not user or not user.is_active or not user.email_verified or not user.is_approved:
            return None
        db.expunge(user)
        return user


def _can_access_session(db, user: User, session_id: str) -> bool:
    """Super admins can watch any session; a therapist only their own
    patients' sessions - same rule enforced everywhere else (see
    auth/dependencies.py, routers/clinical/*)."""
    if user.role != "therapist":
        return True
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return False
    patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
    return bool(patient and patient.responsible_therapist_id == user.id)


@router.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket, session_id: str | None = None):
    """
    Dashboard tabs connect here.
      - dashboard.html (overview/list page) connects with no session_id ->
        receives only lifecycle events (session_connected, session_ended,
        training_started) for any session.
      - session.html (single-session live view) connects with
        ?session_id=<id> -> ALSO receives the high-volume live_update and
        vr_frame stream, but only for that one session.
    """
    # Reject before accept()/connect() if there's no valid session cookie
    # (or bearer token) - previously this endpoint had no auth check at
    # all, so anyone could connect anonymously and receive live session
    # metrics + headset POV frames for any session_id.
    user = await _authenticate_dashboard_ws(websocket)
    if not user:
        await websocket.close(code=4401)
        return

    # An explicit ?session_id= at connect time must belong to this
    # therapist (super admins can watch anything) - same ownership rule
    # as every other session-scoped endpoint.
    if session_id:
        with get_db() as db:
            if not _can_access_session(db, user, session_id):
                await websocket.close(code=4403)
                return

    await dashboard_manager.connect(websocket, session_id=session_id)
    try:
        while True:
            raw = await websocket.receive_text()
            # session.html connects before its session exists, so it
            # can't pass ?session_id= up front - it sends a subscribe
            # message once the session is created instead. Anything else
            # received here (dashboard.html sends nothing) is ignored.
            try:
                msg = json.loads(raw)
                if msg.get("type") == "subscribe" and msg.get("session_id"):
                    sub_id = msg["session_id"]
                    with get_db() as db:
                        if _can_access_session(db, user, sub_id):
                            dashboard_manager.subscribe(websocket, sub_id)
                    # Silently ignored if not owned - same "don't reveal
                    # what exists" posture as the HTTP endpoints (404s
                    # instead of 403s for another therapist's data).
            except (ValueError, TypeError):
                pass
    except WebSocketDisconnect:
        dashboard_manager.disconnect(websocket)
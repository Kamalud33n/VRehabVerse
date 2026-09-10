import uuid
import random
import string

from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Date, Text, Boolean,
    ForeignKey, JSON, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def new_patient_id() -> str:  return new_id("PAT")
def new_session_id() -> str:  return new_id("SES")
def new_device_id() -> str:   return new_id("DEV")
def new_report_id() -> str:   return new_id("RPT")
def new_user_id() -> str:     return new_id("USR")
def new_hospital_id() -> str: return new_id("HOS")
def new_complaint_id() -> str: return new_id("CMP")
def new_reset_token_id() -> str: return new_id("PRT")
def new_verification_token_id() -> str: return new_id("EVT")


def generate_session_code() -> str:
    return "".join(random.choices(string.digits, k=6))


class TimestampMixin:
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class SoftDeleteMixin:
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Auth / User Management
# ---------------------------------------------------------------------------

class Hospital(Base, TimestampMixin):
    __tablename__ = "hospitals"

    id           = Column(String(50), primary_key=True, default=new_hospital_id)
    name         = Column(String(200), nullable=False, index=True)
    address      = Column(String(255), nullable=True)
    city         = Column(String(100), nullable=True)
    country      = Column(String(100), nullable=True)
    phone        = Column(String(20), nullable=True)
    email        = Column(String(100), nullable=True)
    logo_path    = Column(String(255), nullable=True)
    is_active    = Column(Boolean, default=True, nullable=False)

    users = relationship("User", back_populates="hospital")


class User(Base, TimestampMixin, SoftDeleteMixin):
    """
    Authenticated application user. Two roles for now:
      - super_admin: platform-level management (hospitals, approvals, growth)
      - therapist:   the actual clinical/rehab app user, tied to a hospital
    A therapist account is unusable (cannot log in) until:
      1. email_verified is True (therapist proved ownership of the email
         via the OTP sent on registration), AND
      2. status="approved" (an admin reviewed and approved the account).
    """
    __tablename__ = "users"

    id            = Column(String(50), primary_key=True, default=new_user_id)
    full_name     = Column(String(150), nullable=False)
    email         = Column(String(150), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)

    # Email ownership verification. Set once the user submits a valid OTP
    # sent to this address — see EmailVerificationToken below. Login is
    # blocked while this is False, regardless of approval status.
    email_verified    = Column(Boolean, default=False, nullable=False)
    email_verified_at = Column(DateTime, nullable=True)

    country_code  = Column(String(10), nullable=True)   # e.g. "+968"
    phone_number  = Column(String(30), nullable=True)    # local part only, without country code

    role          = Column(String(20), nullable=False, default="therapist", index=True)  # "super_admin" | "therapist"
    job_title     = Column(String(100), nullable=True)   # e.g. "Physiotherapist", "Occupational Therapist"

    hospital_id   = Column(String(50), ForeignKey("hospitals.id"), nullable=True, index=True)

    profile_picture_path = Column(String(255), nullable=True)

    # Approval workflow — only relevant for role="therapist".
    # super_admin accounts are created pre-approved (e.g. via seed/CLI).
    status        = Column(String(20), nullable=False, default="pending", index=True)  # "pending" | "approved" | "rejected"
    approved_by   = Column(String(50), ForeignKey("users.id"), nullable=True)
    approved_at   = Column(DateTime, nullable=True)
    rejection_reason = Column(String(255), nullable=True)

    is_active     = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    hospital = relationship("Hospital", back_populates="users", foreign_keys=[hospital_id])
    complaints = relationship("Complaint", back_populates="user", cascade="all, delete-orphan",
                               foreign_keys="Complaint.user_id")

    @property
    def full_phone(self) -> str | None:
        if self.country_code and self.phone_number:
            return f"{self.country_code}{self.phone_number}"
        return self.phone_number

    @property
    def is_approved(self) -> bool:
        return self.role == "super_admin" or self.status == "approved"


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id         = Column(String(50), primary_key=True, default=new_reset_token_id)
    user_id    = Column(String(50), ForeignKey("users.id"), nullable=False, index=True)
    token      = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class EmailVerificationToken(Base):
    """
    One-time OTP code used to prove ownership of the email address given
    at registration (or when changing email later). Never stores the OTP
    in plaintext — only a salted hash of it (see auth/security.py).
    """
    __tablename__ = "email_verification_tokens"

    id         = Column(String(50), primary_key=True, default=new_verification_token_id)
    user_id    = Column(String(50), ForeignKey("users.id"), nullable=False, index=True)
    email      = Column(String(150), nullable=False)   # snapshot of the address being verified
    otp_hash   = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts   = Column(Integer, default=0, nullable=False)   # failed verify attempts against this code
    used       = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class Complaint(Base, TimestampMixin):
    """Support/complaint ticket raised by a therapist or hospital account."""
    __tablename__ = "complaints"

    id           = Column(String(50), primary_key=True, default=new_complaint_id)
    user_id      = Column(String(50), ForeignKey("users.id"), nullable=False, index=True)
    user_role    = Column(String(20), nullable=False)   # snapshot of role at time of raising
    hospital_id  = Column(String(50), ForeignKey("hospitals.id"), nullable=True)

    subject      = Column(String(200), nullable=False)
    message      = Column(Text, nullable=False)
    status       = Column(String(20), nullable=False, default="open", index=True)  # open | in_progress | resolved
    admin_response = Column(Text, nullable=True)
    resolved_by  = Column(String(50), ForeignKey("users.id"), nullable=True)
    resolved_at  = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="complaints", foreign_keys=[user_id])


class Patient(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "patients"

    id                 = Column(String(50), primary_key=True, default=new_patient_id)
    name               = Column(String(100), nullable=False)
    age                = Column(Integer, nullable=False)
    gender             = Column(String(10), nullable=False)
    weight             = Column(Float, nullable=True)
    height             = Column(Float, nullable=True)
    diagnosis          = Column(String(200), nullable=True)
    affected_body_part = Column(String(100), nullable=True)

    doctor_name        = Column(String(100), nullable=True)
    therapist_name      = Column(String(100), nullable=True)

    # Responsible specialist — links to the authenticated User account.
    # doctor_name/therapist_name (free-text) are kept for backward
    # compatibility and are auto-filled from this user when not supplied.
    responsible_therapist_id = Column(String(50), ForeignKey("users.id"), nullable=True, index=True)
    hospital_id        = Column(String(50), ForeignKey("hospitals.id"), nullable=True, index=True)

    phone              = Column(String(20), nullable=True)
    email              = Column(String(100), nullable=True)
    medical_history    = Column(Text, nullable=True)
    exercise_plan      = Column(Text, nullable=True)
    is_active          = Column(Boolean, default=True, nullable=False)

    # Treatment plan tracking — used to compute per-patient program
    # completion % (completed sessions / planned_total_sessions) in analytics.
    planned_total_sessions = Column(Integer, nullable=True)

    # Therapeutic goals — set by the clinician, compared against actual
    # period averages in the monthly report to show progress vs. target.
    target_accuracy = Column(Float, nullable=True)
    target_rom      = Column(Float, nullable=True)

    sessions = relationship("SessionModel", back_populates="patient", cascade="all, delete-orphan")
    devices  = relationship("VRDevice", back_populates="patient")
    reports  = relationship("Report", back_populates="patient", cascade="all, delete-orphan")


class ExerciseCategory(Base, TimestampMixin):
    __tablename__ = "exercise_categories"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    exercises = relationship("Exercise", back_populates="category")


class Exercise(Base, TimestampMixin):
    __tablename__ = "exercises"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    category_id          = Column(Integer, ForeignKey("exercise_categories.id"), nullable=True, index=True)
    name                 = Column(String(150), nullable=False, unique=True)
    target_body_part     = Column(String(100), nullable=True)
    default_target_reps  = Column(Integer, nullable=True)
    default_target_rom   = Column(Float, nullable=True)
    is_active            = Column(Boolean, default=True, nullable=False)

    category = relationship("ExerciseCategory", back_populates="exercises")


class VRDevice(Base, TimestampMixin):
    __tablename__ = "vr_devices"

    id                = Column(String(50), primary_key=True, default=new_device_id)
    device_identifier = Column(String(150), nullable=False, unique=True, index=True)
    device_name       = Column(String(100), nullable=True)
    patient_id        = Column(String(50), ForeignKey("patients.id"), nullable=True, index=True)
    is_active         = Column(Boolean, default=True, nullable=False)
    last_connected_at = Column(DateTime, nullable=True)

    patient     = relationship("Patient", back_populates="devices")
    connections = relationship("DeviceConnection", back_populates="device", cascade="all, delete-orphan")


class DeviceConnection(Base):
    __tablename__ = "device_connections"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    device_id          = Column(String(50), ForeignKey("vr_devices.id"), nullable=False, index=True)
    session_id         = Column(String(50), ForeignKey("sessions.id"), nullable=True, index=True)
    connected_at       = Column(DateTime, default=func.now(), nullable=False)
    disconnected_at    = Column(DateTime, nullable=True)
    connection_quality = Column(String(20), nullable=True)

    device = relationship("VRDevice", back_populates="connections")


class SessionModel(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sessions"

    id               = Column(String(50), primary_key=True, default=new_session_id)
    patient_id       = Column(String(50), ForeignKey("patients.id"), nullable=False, index=True)
    device_id        = Column(String(50), ForeignKey("vr_devices.id"), nullable=True, index=True)

    doctor_name      = Column(String(100), nullable=True)
    therapist_name   = Column(String(100), nullable=True)
    # Authenticated supervising specialist for this session. doctor_name/
    # therapist_name stay as free-text snapshots (auto-filled from this
    # user's full_name at creation time) so existing report_builder /
    # exports don't need to change.
    supervising_user_id = Column(String(50), ForeignKey("users.id"), nullable=True, index=True)
    exercise_name    = Column(String(150), nullable=False)

    # VR game/level names as reported by the VR app's session summary.
    # exercise_name (above) stays as the dashboard-selected exercise;
    # these mirror what the VR app itself reports for that session.
    game_name  = Column(String(150), nullable=True)
    level_name = Column(String(150), nullable=True)

    status           = Column(String(20), default="pending", nullable=False, index=True)

    # How this session reached "completed", when ended via the dashboard:
    #   "manual"    - therapist/doctor pressed "End Session"
    #   "emergency" - therapist/doctor pressed "Emergency Stop"
    # Left NULL for sessions that end the normal way (VR app's own
    # session_end summary in routers/ws.py never sets this).
    end_reason       = Column(String(20), nullable=True)

    start_time       = Column(DateTime, nullable=True)
    end_time         = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    total_reps       = Column(Integer, nullable=True)
    final_accuracy   = Column(Float, nullable=True)
    final_rom        = Column(Float, nullable=True)

    # Bilateral hand usage — % of reps/activity attributed to each hand
    # during the session. Populated from VR payloads that include hand_side,
    # OR set directly from a VR session-summary payload that already
    # computed the percentages on-device.
    left_hand_usage_pct  = Column(Float, nullable=True)
    right_hand_usage_pct = Column(Float, nullable=True)

    # Discrete count of failed/incorrect attempts during the session
    # (distinct from accuracy %, which is a continuous quality score).
    error_count = Column(Integer, nullable=True)

    # Total number of attempts made during the session (VR summary: AttemptsCount).
    attempts_count = Column(Integer, nullable=True)

    # Average time between stimulus appearance and patient's motor
    # response, averaged across all reps in the session (live-streaming path, ms).
    avg_reaction_time_ms = Column(Float, nullable=True)

    # Average response time as reported directly by a VR session-summary
    # payload (VR reports this in seconds, not ms — kept separate from
    # avg_reaction_time_ms above to avoid unit confusion).
    response_time_sec = Column(Float, nullable=True)

    # VR game score for the session (VR summary: Score).
    score = Column(Integer, nullable=True)

    # Completion % as reported directly by the VR app for the session
    # (VR summary: CompletionPercentage).
    completion_percentage = Column(Float, nullable=True)

    # Highest hand-raise height recorded during the session, in meters
    # (VR summary: MaxHandRaiseHeight).
    max_hand_raise_height = Column(Float, nullable=True)

    # ------------------------------------------------------------------
    # Level 1 (Warm Up) frozen snapshot. The VR app captures these the
    # instant Level 1 finishes (hand raise detected) and never touches
    # them again, so Level 2 (Virtual Store) gameplay can't overwrite or
    # add onto Level 1's numbers. For a two-level session, the plain
    # columns above (score, completion_percentage, error_count,
    # attempts_count, response_time_sec, *_hand_usage_pct,
    # max_hand_raise_height) represent Level 2. Single-level sessions
    # (no Level 2) leave these level1_* columns NULL, same as before.
    # ------------------------------------------------------------------
    level1_score                 = Column(Integer, nullable=True)
    level1_completion_percentage = Column(Float, nullable=True)
    level1_error_count           = Column(Integer, nullable=True)
    level1_attempts_count        = Column(Integer, nullable=True)
    level1_response_time_sec     = Column(Float, nullable=True)
    level1_right_hand_usage_pct  = Column(Float, nullable=True)
    level1_left_hand_usage_pct   = Column(Float, nullable=True)
    level1_max_hand_raise_height = Column(Float, nullable=True)

    # ------------------------------------------------------------------
    # Per-game metric group. Different VR games/levels track completely
    # different things (Virtual Store tracks arm-reach/movement metrics,
    # a future game might track something else entirely) — rather than
    # adding a new fixed column per game forever, game-specific metrics
    # live here as a flexible dict, keyed by field name
    # (see services/game_metrics.py for the registry of known games and
    # their fields). game_type identifies WHICH game's fields are in
    # game_metrics, detected via keyword match against game_name/
    # level_name/exercise_name at session_end — used by report_builder
    # and the dashboard to know which fields to show.
    # ------------------------------------------------------------------
    game_type    = Column(String(50), nullable=True, index=True)
    game_metrics = Column(JSON, nullable=True)

    therapist_notes  = Column(Text, nullable=True)
    recommendations  = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="sessions")
    device  = relationship("VRDevice")

    session_code = relationship("SessionCode", back_populates="session", uselist=False,
                                 cascade="all, delete-orphan")
    live_metrics = relationship("LiveMetric", back_populates="session", cascade="all, delete-orphan")
    results      = relationship("ExerciseResult", back_populates="session", cascade="all, delete-orphan")
    reports      = relationship("Report", back_populates="session")
    finger_tracking  = relationship("FingerTrackingData", back_populates="session", cascade="all, delete-orphan")
    device_telemetry = relationship("DeviceTelemetry", back_populates="session", cascade="all, delete-orphan")


class SessionCode(Base):
    __tablename__ = "session_codes"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), ForeignKey("sessions.id"), nullable=False, unique=True)
    code       = Column(String(6), nullable=False, index=True)
    is_used    = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    used_at    = Column(DateTime, nullable=True)

    session = relationship("SessionModel", back_populates="session_code")

    __table_args__ = (
        Index("ix_session_codes_code_active", "code", "is_used"),
    )


class LiveMetric(Base):
    __tablename__ = "live_metrics"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    session_id   = Column(String(50), ForeignKey("sessions.id"), nullable=False, index=True)

    exercise     = Column(String(150), nullable=False)
    rep_count    = Column(Integer, nullable=False)
    accuracy     = Column(Float, nullable=False)
    rom          = Column(Float, nullable=False)
    status       = Column(String(30), nullable=False)
    elapsed_time = Column(Integer, nullable=False)

    # Which hand this rep/metric update is attributed to.
    # "left" | "right" | "both" | None (unknown / device didn't report it).
    hand_side = Column(String(10), nullable=True)

    # Time from stimulus/cue to patient's motor response, for this rep.
    reaction_time_ms = Column(Float, nullable=True)

    # Whether this rep counted as a failed/incorrect attempt.
    is_error = Column(Boolean, default=False, nullable=False)

    received_at      = Column(DateTime, default=func.now(), nullable=False)
    client_timestamp = Column(String(50), nullable=True)

    session = relationship("SessionModel", back_populates="live_metrics")

    __table_args__ = (
        Index("ix_live_metrics_session_received", "session_id", "received_at"),
    )


class FingerTrackingData(Base):
    """
    Per-finger flexion / smart-glove data. One row per glove reading
    received during a session. Fully optional — sessions without glove
    hardware simply never write to this table.
    """
    __tablename__ = "finger_tracking_data"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    session_id     = Column(String(50), ForeignKey("sessions.id"), nullable=False, index=True)
    live_metric_id = Column(Integer, ForeignKey("live_metrics.id"), nullable=True, index=True)

    hand_side = Column(String(10), nullable=True)  # "left" | "right"

    # e.g. {"thumb": 82.5, "index": 91.0, "middle": 88.0, "ring": 76.5, "pinky": 70.0}
    finger_flexion = Column(JSON, nullable=True)

    received_at = Column(DateTime, default=func.now(), nullable=False)

    session = relationship("SessionModel", back_populates="finger_tracking")

    __table_args__ = (
        Index("ix_finger_tracking_session_received", "session_id", "received_at"),
    )


class DeviceTelemetry(Base):
    """
    Technical/engineering KPIs reported by the VR headset itself
    (FPS, load time, network latency) — separate from clinical metrics,
    used for platform health monitoring rather than patient progress.
    """
    __tablename__ = "device_telemetry"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), ForeignKey("sessions.id"), nullable=False, index=True)

    fps            = Column(Float, nullable=True)
    load_time_ms   = Column(Float, nullable=True)
    latency_ms     = Column(Float, nullable=True)

    recorded_at = Column(DateTime, default=func.now(), nullable=False)

    session = relationship("SessionModel", back_populates="device_telemetry")

    __table_args__ = (
        Index("ix_device_telemetry_session_recorded", "session_id", "recorded_at"),
    )


class ExerciseResult(Base):
    __tablename__ = "exercise_results"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    session_id        = Column(String(50), ForeignKey("sessions.id"), nullable=False, index=True)
    exercise_name     = Column(String(150), nullable=False)
    repetition_number = Column(Integer, nullable=False)
    accuracy          = Column(Float, nullable=True)
    rom_achieved      = Column(Float, nullable=True)
    is_completed      = Column(Boolean, default=False, nullable=False)
    feedback          = Column(String(255), nullable=True)
    timestamp         = Column(DateTime, default=func.now(), nullable=False)

    session = relationship("SessionModel", back_populates="results")


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id           = Column(String(50), primary_key=True, default=new_report_id)
    # Nullable: admin reports (report_type="admin") are department-level,
    # not tied to a single patient.
    patient_id   = Column(String(50), ForeignKey("patients.id"), nullable=True, index=True)
    session_id   = Column(String(50), ForeignKey("sessions.id"), nullable=True, index=True)
    report_type  = Column(String(50), nullable=False, default="session_summary")
    file_path    = Column(String(255), nullable=True)
    generated_by = Column(String(100), nullable=True)

    # Set only for aggregated multi-session reports (report_type in
    # "range"/"monthly"/"admin"), e.g. weekly/monthly downloads spanning
    # several sessions. Left NULL for normal single-session reports.
    period_start = Column(Date, nullable=True)
    period_end   = Column(Date, nullable=True)

    # Only set for report_type="comparative": the list of periods being
    # compared, e.g. [{"label": "May", "start": "2026-05-01", "end": "2026-05-31"}, ...]
    periods = Column(JSON, nullable=True)

    patient = relationship("Patient", back_populates="reports")
    session = relationship("SessionModel", back_populates="reports")


class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    title      = Column(String(150), nullable=False)
    message    = Column(Text, nullable=True)
    category   = Column(String(50), nullable=True)
    is_read    = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    entity_type  = Column(String(50), nullable=False)
    entity_id    = Column(String(50), nullable=False)
    action       = Column(String(50), nullable=False)
    performed_by = Column(String(100), nullable=True)
    details      = Column(JSON, nullable=True)
    timestamp    = Column(DateTime, default=func.now(), nullable=False)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    actor       = Column(String(100), nullable=True)
    action      = Column(String(255), nullable=False)
    entity_type = Column(String(50), nullable=True)
    entity_id   = Column(String(50), nullable=True)
    timestamp   = Column(DateTime, default=func.now(), nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    key         = Column(String(50), unique=True, nullable=False)
    value       = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at  = Column(DateTime, default=func.now(), onupdate=func.now())
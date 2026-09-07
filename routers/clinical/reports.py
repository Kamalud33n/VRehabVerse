import os
import re
import calendar
import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import get_db
from config import REPORTS_DIR
from models import Report, Patient, SessionModel, VRDevice, Exercise, ExerciseCategory, User
from services.report_builder import (
    build_report_sync, build_range_report_sync,
    build_monthly_report_sync, build_comparative_report_sync, build_admin_report_sync,
)
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _resolve_generated_by(current_user: User, override: str | None) -> str:
    """The supervising specialist is identified from the authenticated
    session and should never need to be re-typed. `override` only exists
    so a Super Admin can attribute a report differently if needed."""
    if override:
        return override
    return current_user.full_name


def _get_owned_patient(db, patient_id: str, current_user: User) -> Patient:
    """Loads a patient and enforces that a therapist can only reach their
    own patients' reports. Super admins can reach any patient. 404 (not
    403) on mismatch so a therapist can't probe for another therapist's
    patient by id."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
        raise HTTPException(404, "Patient not found")
    return patient


def _get_owned_session_for_report(db, session_id: str, current_user: User) -> SessionModel:
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    if current_user.role == "therapist":
        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        if not patient or patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Session not found")
    return session


def _get_owned_report(db, report_id: str, current_user: User) -> Report:
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    if current_user.role == "therapist":
        # Admin-level reports (patient_id is None) aren't therapist-owned
        # at all — a plain therapist should never be able to reach one.
        if not report.patient_id:
            raise HTTPException(404, "Report not found")
        patient = db.query(Patient).filter(Patient.id == report.patient_id).first()
        if not patient or patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Report not found")
    return report


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_")


def _safe_filename(patient_name: str, date_obj, game_name: str | None = None,
                    session_id: str | None = None) -> str:
    """Builds the download filename shown to the doctor.

    Session-level reports (game_name/session_id present) get
    'PatientName_GameName_SessionId_YYYY-MM-DD.pdf' so the game and the
    exact session are identifiable from the filename alone, without
    opening the PDF. Aggregate reports (range/monthly/comparative/admin)
    have no single game/session, so they keep the plain
    'PatientName_YYYY-MM-DD.pdf' form."""
    clean_name = _slug(patient_name) or "Patient"
    date_str = date_obj.strftime("%Y-%m-%d")
    parts = [clean_name]
    if game_name:
        parts.append(_slug(game_name))
    if session_id:
        parts.append(_slug(session_id))
    parts.append(date_str)
    return "_".join(parts) + ".pdf"


@router.post("/generate/{session_id}")
def generate_report(session_id: str, generated_by: str | None = None,
                     current_user: User = Depends(get_current_user)):
    generated_by = _resolve_generated_by(current_user, generated_by)
    with get_db() as db:
        session = _get_owned_session_for_report(db, session_id, current_user)
        patient = db.query(Patient).filter(Patient.id == session.patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")

        report = Report(
            patient_id=patient.id,
            session_id=session.id,
            report_type="session_summary",
            generated_by=generated_by,
        )
        db.add(report)
        db.flush()

        file_path = os.path.join(REPORTS_DIR, f"{report.id}.pdf")
        build_report_sync(patient, session, file_path, report_id=report.id, generated_by=generated_by)

        report.file_path = file_path
        db.commit()

        return {"report_id": report.id, "file_path": file_path}


@router.post("/generate-range")
def generate_range_report(
    patient_id: str,
    start_date: str,
    end_date: str,
    generated_by: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Aggregates every completed session for one patient within
    [start_date, end_date] (inclusive) into a single PDF — used for
    weekly / monthly / custom-range downloads instead of one session."""
    generated_by = _resolve_generated_by(current_user, generated_by)
    try:
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "start_date and end_date must be in YYYY-MM-DD format")

    if start > end:
        raise HTTPException(400, "start_date must be before end_date")

    with get_db() as db:
        patient = _get_owned_patient(db, patient_id, current_user)

        range_start = datetime.datetime.combine(start, datetime.time.min)
        range_end = datetime.datetime.combine(end, datetime.time.max)

        sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.patient_id == patient_id,
                SessionModel.status == "completed",
                SessionModel.created_at >= range_start,
                SessionModel.created_at <= range_end,
            )
            .order_by(SessionModel.created_at)
            .all()
        )

        if not sessions:
            raise HTTPException(400, "No completed sessions found in this date range.")

        report = Report(
            patient_id=patient.id,
            session_id=None,
            report_type="range",
            generated_by=generated_by,
            period_start=start,
            period_end=end,
        )
        db.add(report)
        db.flush()

        file_path = os.path.join(REPORTS_DIR, f"{report.id}.pdf")
        build_range_report_sync(
            patient, sessions, start, end, file_path,
            report_id=report.id, generated_by=generated_by,
        )

        report.file_path = file_path
        db.commit()

        return {"report_id": report.id, "file_path": file_path, "session_count": len(sessions)}


class ComparativePeriod(BaseModel):
    label: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD


class ComparativeRequest(BaseModel):
    patient_id: str
    generated_by: str | None = None
    periods: list[ComparativePeriod] = Field(min_length=2)


def _parse_ymd(value: str, field_name: str) -> datetime.date:
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"{field_name} must be in YYYY-MM-DD format")


@router.post("/generate-monthly")
def generate_monthly_report(
    patient_id: str,
    month: str,  # "YYYY-MM"
    generated_by: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Comprehensive month-in-review for one patient: month's session
    average vs. the patient's therapeutic goals (target_accuracy / target_rom)."""
    generated_by = _resolve_generated_by(current_user, generated_by)
    try:
        month_start = datetime.datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(400, "month must be in YYYY-MM format")
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    with get_db() as db:
        patient = _get_owned_patient(db, patient_id, current_user)

        range_start = datetime.datetime.combine(month_start, datetime.time.min)
        range_end = datetime.datetime.combine(month_end, datetime.time.max)

        sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.patient_id == patient_id,
                SessionModel.status == "completed",
                SessionModel.created_at >= range_start,
                SessionModel.created_at <= range_end,
            )
            .order_by(SessionModel.created_at)
            .all()
        )

        if not sessions:
            raise HTTPException(400, "No completed sessions found in this month.")

        report = Report(
            patient_id=patient.id,
            session_id=None,
            report_type="monthly",
            generated_by=generated_by,
            period_start=month_start,
            period_end=month_end,
        )
        db.add(report)
        db.flush()

        file_path = os.path.join(REPORTS_DIR, f"{report.id}.pdf")
        build_monthly_report_sync(
            patient, sessions, month_start, month_end, file_path,
            report_id=report.id, generated_by=generated_by,
        )

        report.file_path = file_path
        db.commit()

        return {"report_id": report.id, "file_path": file_path, "session_count": len(sessions)}


@router.post("/generate-comparative")
def generate_comparative_report(body: ComparativeRequest, current_user: User = Depends(get_current_user)):
    """Compares one patient's performance across several caller-defined
    periods (e.g. month over month) to show the overall progress trend."""
    resolved_generated_by = _resolve_generated_by(current_user, body.generated_by)
    with get_db() as db:
        patient = _get_owned_patient(db, body.patient_id, current_user)

        periods_for_builder = []
        periods_for_storage = []
        for p in body.periods:
            start = _parse_ymd(p.start_date, "start_date")
            end = _parse_ymd(p.end_date, "end_date")
            if start > end:
                raise HTTPException(400, f"Period '{p.label}': start_date must be before end_date")

            range_start = datetime.datetime.combine(start, datetime.time.min)
            range_end = datetime.datetime.combine(end, datetime.time.max)
            sessions = (
                db.query(SessionModel)
                .filter(
                    SessionModel.patient_id == body.patient_id,
                    SessionModel.status == "completed",
                    SessionModel.created_at >= range_start,
                    SessionModel.created_at <= range_end,
                )
                .order_by(SessionModel.created_at)
                .all()
            )
            if not sessions:
                raise HTTPException(400, f"No completed sessions found for period '{p.label}'.")

            periods_for_builder.append({"label": p.label, "start": start, "end": end, "sessions": sessions})
            periods_for_storage.append({"label": p.label, "start": p.start_date, "end": p.end_date})

        overall_start = min(pd["start"] for pd in periods_for_builder)
        overall_end = max(pd["end"] for pd in periods_for_builder)

        report = Report(
            patient_id=patient.id,
            session_id=None,
            report_type="comparative",
            generated_by=resolved_generated_by,
            period_start=overall_start,
            period_end=overall_end,
            periods=periods_for_storage,
        )
        db.add(report)
        db.flush()

        file_path = os.path.join(REPORTS_DIR, f"{report.id}.pdf")
        build_comparative_report_sync(
            patient, periods_for_builder, file_path,
            report_id=report.id, generated_by=resolved_generated_by,
        )

        report.file_path = file_path
        db.commit()

        return {"report_id": report.id, "file_path": file_path, "period_count": len(periods_for_builder)}


@router.post("/generate-admin")
def generate_admin_report(
    start_date: str,
    end_date: str,
    generated_by: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Department-level operational report — not tied to one patient:
    session volume, completion rate, device utilization, and which
    exercise categories saw the most engagement over the period."""
    generated_by = _resolve_generated_by(current_user, generated_by)
    start = _parse_ymd(start_date, "start_date")
    end = _parse_ymd(end_date, "end_date")
    if start > end:
        raise HTTPException(400, "start_date must be before end_date")

    with get_db() as db:
        range_start = datetime.datetime.combine(start, datetime.time.min)
        range_end = datetime.datetime.combine(end, datetime.time.max)

        all_sessions = (
            db.query(SessionModel)
            .filter(SessionModel.created_at >= range_start, SessionModel.created_at <= range_end)
            .all()
        )
        if not all_sessions:
            raise HTTPException(400, "No sessions found in this date range.")

        completed_sessions = [s for s in all_sessions if s.status == "completed"]
        active_patients = len({s.patient_id for s in all_sessions})
        active_devices = len({s.device_id for s in all_sessions if s.device_id})
        total_devices = db.query(VRDevice).count()

        # Map exercise_name -> category, so session counts can be rolled
        # up by category even though sessions only store the exercise name.
        exercise_rows = db.query(Exercise.name, ExerciseCategory.name).outerjoin(
            ExerciseCategory, Exercise.category_id == ExerciseCategory.id
        ).all()
        name_to_category = {name: (cat or "Uncategorized") for name, cat in exercise_rows}

        category_counts: dict[str, int] = {}
        for s in all_sessions:
            cat = name_to_category.get(s.exercise_name, "Uncategorized")
            category_counts[cat] = category_counts.get(cat, 0) + 1
        categories = sorted(
            [{"name": name, "count": count} for name, count in category_counts.items()],
            key=lambda c: c["count"], reverse=True,
        )

        stats = {
            "total_sessions": len(all_sessions),
            "completed_sessions": len(completed_sessions),
            "active_patients": active_patients,
            "active_devices": active_devices,
            "total_devices": total_devices,
            "categories": categories,
        }

        report = Report(
            patient_id=None,
            session_id=None,
            report_type="admin",
            generated_by=generated_by,
            period_start=start,
            period_end=end,
        )
        db.add(report)
        db.flush()

        file_path = os.path.join(REPORTS_DIR, f"{report.id}.pdf")
        build_admin_report_sync(
            start, end, stats, file_path,
            report_id=report.id, generated_by=generated_by,
        )

        report.file_path = file_path
        db.commit()

        return {"report_id": report.id, "file_path": file_path, "session_count": len(all_sessions)}


@router.get("")
def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    patient_id: str | None = None,
    current_user: User = Depends(get_current_user),
):
    with get_db() as db:
        q = db.query(Report)

        # Therapists only ever see reports for their own patients
        # (admin-level, patient-less reports are excluded for them too).
        if current_user.role == "therapist":
            q = q.join(Patient, Patient.id == Report.patient_id).filter(
                Patient.responsible_therapist_id == current_user.id
            )

        if patient_id:
            q = q.filter(Report.patient_id == patient_id)
        q = q.order_by(Report.created_at.desc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": r.id,
                    "patient_id": r.patient_id,
                    "session_id": r.session_id,
                    "report_type": r.report_type,
                    "generated_by": r.generated_by,
                    "created_at": r.created_at,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "periods": r.periods,
                }
                for r in items
            ],
        }


@router.get("/{report_id}/download")
def download_report(report_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        report = _get_owned_report(db, report_id, current_user)
        if not report.file_path or not os.path.exists(report.file_path):
            raise HTTPException(404, "Report file not found")

        patient = db.query(Patient).filter(Patient.id == report.patient_id).first() if report.patient_id else None
        patient_name = patient.name if patient else ("Department" if report.report_type == "admin" else "Patient")
        report_date = report.period_end or report.created_at.date()

        # Session-level reports carry a single game/exercise — surface it
        # (and the session id) in the filename so the doctor can tell
        # which game a downloaded report belongs to without opening it.
        game_name = None
        session_id = None
        if report.report_type == "session_summary" and report.session_id:
            session = db.query(SessionModel).filter(SessionModel.id == report.session_id).first()
            if session:
                game_name = session.game_name or session.exercise_name
                session_id = session.id
                report_date = session.created_at.date()

        download_name = _safe_filename(patient_name, report_date, game_name, session_id)

        return FileResponse(report.file_path, media_type="application/pdf",
                             filename=download_name)


@router.delete("/{report_id}")
def delete_report(report_id: str, current_user: User = Depends(get_current_user)):
    """Deletes a generated report: removes the DB row and, if present,
    the PDF file on disk. Same ownership rule as the other report
    endpoints — a therapist can only delete reports for their own
    patients; admin-level reports require a super admin."""
    with get_db() as db:
        report = _get_owned_report(db, report_id, current_user)

        file_path = report.file_path
        db.delete(report)
        db.commit()

        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        return {"deleted": True, "report_id": report_id}


@router.get("/{report_id}/preview")
def preview_report(report_id: str, current_user: User = Depends(get_current_user)):
    """Same PDF as /download, but without Content-Disposition: attachment,
    so browsers render it inline (used by the Reports page preview modal
    instead of forcing a download)."""
    with get_db() as db:
        report = _get_owned_report(db, report_id, current_user)
        if not report.file_path or not os.path.exists(report.file_path):
            raise HTTPException(404, "Report file not found")

        return FileResponse(report.file_path, media_type="application/pdf")
import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy import func
from pydantic import BaseModel

from database import get_db
from models import User, Hospital, Complaint, AuditLog, Patient, SessionModel
from auth.dependencies import require_super_admin
from mail.mailer import send_mail
from mail.templates import registration_approved_email

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Dashboard - product growth / high-level stats
# ---------------------------------------------------------------------------
@router.get("/dashboard/overview")
def admin_overview(admin: User = Depends(require_super_admin)):
    with get_db() as db:
        total_hospitals = db.query(Hospital).filter(Hospital.is_active == True).count()
        total_therapists = db.query(User).filter(
            User.role == "therapist", User.is_deleted == False, User.status == "approved"
        ).count()
        pending_approvals = db.query(User).filter(
            User.role == "therapist", User.is_deleted == False, User.status == "pending"
        ).count()
        open_complaints = db.query(Complaint).filter(Complaint.status != "resolved").count()
        total_patients = db.query(Patient).filter(Patient.is_deleted == False).count()
        total_sessions = db.query(SessionModel).filter(SessionModel.is_deleted == False).count()

        return {
            "total_hospitals": total_hospitals,
            "total_therapists": total_therapists,
            "pending_approvals": pending_approvals,
            "open_complaints": open_complaints,
            "total_patients": total_patients,
            "total_sessions": total_sessions,
        }


@router.get("/dashboard/registration-trend")
def registration_trend(days: int = Query(30, ge=1, le=365), admin: User = Depends(require_super_admin)):
    """Daily new-therapist-registration counts, for the growth chart."""
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        rows = (
            db.query(func.date(User.created_at), func.count(User.id))
            .filter(User.role == "therapist", User.created_at >= since, User.is_deleted == False)
            .group_by(func.date(User.created_at))
            .order_by(func.date(User.created_at))
            .all()
        )
        return [{"date": str(d), "count": c} for d, c in rows]


@router.get("/dashboard/hospital-growth")
def hospital_growth(days: int = Query(90, ge=1, le=365), admin: User = Depends(require_super_admin)):
    with get_db() as db:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        rows = (
            db.query(func.date(Hospital.created_at), func.count(Hospital.id))
            .filter(Hospital.created_at >= since)
            .group_by(func.date(Hospital.created_at))
            .order_by(func.date(Hospital.created_at))
            .all()
        )
        return [{"date": str(d), "count": c} for d, c in rows]


# ---------------------------------------------------------------------------
# Registration approvals
# ---------------------------------------------------------------------------
@router.get("/registrations")
def list_registrations(
    status: str = Query("pending", pattern="^(pending|approved|rejected|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_super_admin),
):
    with get_db() as db:
        q = db.query(User).filter(User.role == "therapist", User.is_deleted == False)
        if status != "all":
            q = q.filter(User.status == status)
        q = q.order_by(User.created_at.desc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": u.id,
                    "full_name": u.full_name,
                    "email": u.email,
                    "email_verified": u.email_verified,
                    "phone": u.full_phone,
                    "job_title": u.job_title,
                    "status": u.status,
                    "hospital_name": u.hospital.name if u.hospital else None,
                    "created_at": u.created_at,
                }
                for u in items
            ],
        }


class RejectPayload(BaseModel):
    reason: str | None = None


@router.post("/registrations/{user_id}/approve")
def approve_registration(user_id: str, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        user = db.query(User).filter(User.id == user_id, User.role == "therapist").first()
        if not user:
            raise HTTPException(404, "Registration not found")
        if user.status == "approved":
            raise HTTPException(400, "Already approved")
        if not user.email_verified:
            raise HTTPException(400, "This therapist hasn't verified their email yet — approval isn't allowed until they do.")

        user.status = "approved"
        user.approved_by = admin.id
        user.approved_at = datetime.datetime.utcnow()
        user.rejection_reason = None

        db.add(AuditLog(entity_type="User", entity_id=user.id, action="registration_approved",
                         performed_by=admin.id, details={"approved_user": user.email}))

        # Grab these before commit - the ORM object may expire after commit
        # depending on session config, and we still need them for the email.
        approved_email = user.email
        approved_name = user.full_name

        db.commit()

        subject, html_body = registration_approved_email(approved_name)
        send_mail(approved_email, subject, html_body)

        return {"status": "approved", "user_id": user.id}


@router.post("/registrations/{user_id}/reject")
def reject_registration(user_id: str, payload: RejectPayload, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        user = db.query(User).filter(User.id == user_id, User.role == "therapist").first()
        if not user:
            raise HTTPException(404, "Registration not found")

        user.status = "rejected"
        user.rejection_reason = payload.reason
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="registration_rejected",
                         performed_by=admin.id, details={"reason": payload.reason}))
        db.commit()
        return {"status": "rejected", "user_id": user.id}


# ---------------------------------------------------------------------------
# Hospitals
# ---------------------------------------------------------------------------
class HospitalPayload(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None


@router.get("/hospitals")
def list_hospitals(search: str | None = None, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        q = db.query(Hospital)
        if search:
            q = q.filter(Hospital.name.ilike(f"%{search}%"))
        rows = q.order_by(Hospital.name).all()
        return [
            {
                "id": h.id, "name": h.name, "city": h.city, "country": h.country,
                "phone": h.phone, "email": h.email, "is_active": h.is_active,
                "therapist_count": db.query(User).filter(User.hospital_id == h.id, User.is_deleted == False).count(),
            }
            for h in rows
        ]


@router.post("/hospitals")
def create_hospital(payload: HospitalPayload, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        hospital = Hospital(**payload.model_dump())
        db.add(hospital)
        db.commit()
        db.refresh(hospital)
        return {"id": hospital.id, "name": hospital.name}


# ---------------------------------------------------------------------------
# Customer management (therapists + hospitals)
# ---------------------------------------------------------------------------
@router.get("/customers/therapists")
def list_therapist_customers(
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_super_admin),
):
    with get_db() as db:
        q = db.query(User).filter(User.role == "therapist", User.is_deleted == False)
        if search:
            like = f"%{search}%"
            q = q.filter(User.full_name.ilike(like) | User.email.ilike(like))
        q = q.order_by(User.created_at.desc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [
                {
                    "id": u.id, "full_name": u.full_name, "email": u.email,
                    "phone": u.full_phone, "job_title": u.job_title, "status": u.status,
                    "is_active": u.is_active, "hospital_name": u.hospital.name if u.hospital else None,
                    "last_login_at": u.last_login_at, "created_at": u.created_at,
                }
                for u in items
            ],
        }


@router.get("/customers/therapists/{user_id}")
def get_therapist_customer(user_id: str, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        u = db.query(User).filter(User.id == user_id, User.role == "therapist").first()
        if not u:
            raise HTTPException(404, "Therapist not found")

        patient_count = db.query(Patient).filter(Patient.responsible_therapist_id == u.id).count()
        session_count = db.query(SessionModel).filter(SessionModel.supervising_user_id == u.id).count()

        return {
            "id": u.id, "full_name": u.full_name, "email": u.email, "phone": u.full_phone,
            "job_title": u.job_title, "status": u.status, "is_active": u.is_active,
            "profile_picture_path": u.profile_picture_path,
            "hospital": {
                "id": u.hospital.id, "name": u.hospital.name, "city": u.hospital.city,
                "logo_path": u.hospital.logo_path,
            } if u.hospital else None,
            "patient_count": patient_count,
            "session_count": session_count,
            "created_at": u.created_at,
            "last_login_at": u.last_login_at,
        }


@router.post("/customers/therapists/{user_id}/deactivate")
def deactivate_therapist(user_id: str, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        u = db.query(User).filter(User.id == user_id, User.role == "therapist").first()
        if not u:
            raise HTTPException(404, "Therapist not found")
        u.is_active = False
        db.add(AuditLog(entity_type="User", entity_id=u.id, action="deactivated", performed_by=admin.id))
        db.commit()
        return {"status": "deactivated"}


@router.post("/customers/therapists/{user_id}/reactivate")
def reactivate_therapist(user_id: str, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        u = db.query(User).filter(User.id == user_id, User.role == "therapist").first()
        if not u:
            raise HTTPException(404, "Therapist not found")
        u.is_active = True
        db.add(AuditLog(entity_type="User", entity_id=u.id, action="reactivated", performed_by=admin.id))
        db.commit()
        return {"status": "reactivated"}


# ---------------------------------------------------------------------------
# Complaints / support (admin side)
# ---------------------------------------------------------------------------
class ComplaintResponsePayload(BaseModel):
    response: str
    status: str = "resolved"  # "in_progress" | "resolved"


@router.get("/complaints")
def list_complaints(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_super_admin),
):
    with get_db() as db:
        q = db.query(Complaint)
        if status:
            q = q.filter(Complaint.status == status)
        q = q.order_by(Complaint.created_at.desc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [
                {
                    "id": c.id, "subject": c.subject, "message": c.message,
                    "status": c.status, "user_role": c.user_role,
                    "raised_by": c.user.full_name if c.user else None,
                    "hospital_name": c.user.hospital.name if c.user and c.user.hospital else None,
                    "admin_response": c.admin_response, "created_at": c.created_at,
                }
                for c in items
            ],
        }


@router.post("/complaints/{complaint_id}/respond")
def respond_to_complaint(complaint_id: str, payload: ComplaintResponsePayload, admin: User = Depends(require_super_admin)):
    with get_db() as db:
        c = db.query(Complaint).filter(Complaint.id == complaint_id).first()
        if not c:
            raise HTTPException(404, "Complaint not found")

        c.admin_response = payload.response
        c.status = payload.status
        if payload.status == "resolved":
            c.resolved_by = admin.id
            c.resolved_at = datetime.datetime.utcnow()

        db.add(AuditLog(entity_type="Complaint", entity_id=c.id, action="complaint_responded", performed_by=admin.id))
        db.commit()
        return {"status": c.status}
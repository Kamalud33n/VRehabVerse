from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from database import get_db
from models import User, Complaint, AuditLog
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/support", tags=["support"])


class ComplaintCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=5)


@router.post("")
def create_complaint(payload: ComplaintCreate, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        complaint = Complaint(
            user_id=current_user.id,
            user_role=current_user.role,
            hospital_id=current_user.hospital_id,
            subject=payload.subject,
            message=payload.message,
            status="open",
        )
        db.add(complaint)
        db.flush()
        db.add(AuditLog(entity_type="Complaint", entity_id=complaint.id, action="complaint_created",
                         performed_by=current_user.id))
        db.commit()
        return {"status": "submitted", "complaint_id": complaint.id}


@router.get("")
def list_my_complaints(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """A therapist sees only their own complaints. Super Admins see all -
    (admins typically use the richer /api/admin/complaints endpoint, but
    this stays consistent if they hit this one too)."""
    with get_db() as db:
        q = db.query(Complaint)
        if current_user.role != "super_admin":
            q = q.filter(Complaint.user_id == current_user.id)
        q = q.order_by(Complaint.created_at.desc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [
                {
                    "id": c.id, "subject": c.subject, "message": c.message,
                    "status": c.status, "admin_response": c.admin_response,
                    "created_at": c.created_at, "resolved_at": c.resolved_at,
                }
                for c in items
            ],
        }


@router.get("/{complaint_id}")
def get_complaint(complaint_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        c = db.query(Complaint).filter(Complaint.id == complaint_id).first()
        if not c:
            raise HTTPException(404, "Complaint not found")
        if current_user.role != "super_admin" and c.user_id != current_user.id:
            raise HTTPException(403, "You do not have access to this complaint")

        return {
            "id": c.id, "subject": c.subject, "message": c.message,
            "status": c.status, "admin_response": c.admin_response,
            "created_at": c.created_at, "resolved_at": c.resolved_at,
        }
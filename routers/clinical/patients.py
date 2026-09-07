import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from database import get_db
from models import Patient, ActivityLog, User
from auth.dependencies import get_current_user

router = APIRouter(prefix="/api/patients", tags=["patients"])

# Whitelist for ?sort_by= - getattr(Patient, sort_by, ...) previously
# accepted ANY string, so an unknown/relationship/dunder name would either
# 500 when .desc()/.asc() was called on a non-column, or expose internal
# attribute/column names that aren't meant to be queried this way.
PATIENT_SORT_FIELDS = {"created_at", "name", "id", "phone", "is_active", "updated_at"}


class PatientCreate(BaseModel):
    name: str
    age: int
    gender: str
    weight: float | None = None
    height: float | None = None
    diagnosis: str | None = None
    affected_body_part: str | None = None
    doctor_name: str | None = None
    therapist_name: str | None = None
    phone: str | None = None
    email: str | None = None
    medical_history: str | None = None
    exercise_plan: str | None = None


class PatientUpdate(PatientCreate):
    is_active: bool = True


class PatientOut(BaseModel):
    id: str
    name: str
    age: int
    gender: str
    weight: float | None
    height: float | None
    diagnosis: str | None
    affected_body_part: str | None
    doctor_name: str | None
    therapist_name: str | None
    phone: str | None
    email: str | None
    medical_history: str | None
    exercise_plan: str | None
    is_active: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True


@router.get("")
def list_patients(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    include_inactive: bool = False,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    current_user: User = Depends(get_current_user),
):
    with get_db() as db:
        q = db.query(Patient).filter(Patient.is_deleted == False)

        # Therapists only ever see their own patients. Super admins get the
        # full list (needed for oversight / customer-management screens).
        if current_user.role == "therapist":
            q = q.filter(Patient.responsible_therapist_id == current_user.id)

        if not include_inactive:
            q = q.filter(Patient.is_active == True)
        if search:
            like = f"%{search}%"
            q = q.filter(
                Patient.name.ilike(like) | Patient.id.ilike(like) | Patient.phone.ilike(like)
            )

        sort_by = sort_by if sort_by in PATIENT_SORT_FIELDS else "created_at"
        sort_col = getattr(Patient, sort_by)
        q = q.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [PatientOut.model_validate(p) for p in items],
        }


@router.post("", response_model=PatientOut)
def create_patient(payload: PatientCreate, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        if payload.email and db.query(Patient).filter(Patient.email == payload.email).first():
            raise HTTPException(400, "Email already registered")
        if payload.phone and db.query(Patient).filter(Patient.phone == payload.phone).first():
            raise HTTPException(400, "Phone number already registered")

        data = payload.model_dump()
        if current_user.role == "therapist":
            data["responsible_therapist_id"] = current_user.id
            data.setdefault("hospital_id", None)
            data["hospital_id"] = current_user.hospital_id
            if not data.get("therapist_name"):
                data["therapist_name"] = current_user.full_name

        patient = Patient(**data)
        db.add(patient)
        db.flush()
        db.add(ActivityLog(action=f"Patient {patient.name} created", entity_type="Patient", entity_id=patient.id))
        db.commit()
        db.refresh(patient)
        return patient


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Patient not found")
        return patient


@router.put("/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: str, payload: PatientUpdate, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Patient not found")

        for field, value in payload.model_dump().items():
            setattr(patient, field, value)

        db.add(ActivityLog(action=f"Patient {patient.name} updated", entity_type="Patient", entity_id=patient.id))
        db.commit()
        db.refresh(patient)
        return patient


@router.delete("/{patient_id}")
def delete_patient(patient_id: str, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
        if current_user.role == "therapist" and patient.responsible_therapist_id != current_user.id:
            raise HTTPException(404, "Patient not found")

        patient.is_active = False
        patient.is_deleted = True
        patient.deleted_at = datetime.datetime.utcnow()
        db.add(ActivityLog(action=f"Patient {patient.name} deactivated", entity_type="Patient", entity_id=patient.id))
        db.commit()
        return {"status": "deactivated"}
import datetime

from fastapi import APIRouter, HTTPException

from database import get_db
from models import User, Hospital, AuditLog, EmailVerificationToken
from auth.schemas import RegisterRequest
from auth.security import hash_password, generate_otp, hash_otp, EMAIL_OTP_EXPIRE_MINUTES, IS_DEV_ENV
from auth.email_validation import validate_registration_email
from mail.mailer import send_mail
from mail.templates import email_verification_otp_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register(payload: RegisterRequest):
    # Backend-enforced email checks — never trust the frontend for these.
    # 1) EmailStr on RegisterRequest already rejected malformed syntax.
    # 2) This additionally blocks disposable domains and domains with no
    #    mail-exchanger (i.e. addresses that can't possibly receive mail).
    email = validate_registration_email(payload.email)

    with get_db() as db:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            raise HTTPException(status_code=400, detail="An account with this email already exists")

        # Hospital: link to an existing one by id, or create a new one
        # inline if a name was provided instead.
        hospital = None
        if payload.hospital_id:
            hospital = db.query(Hospital).filter(Hospital.id == payload.hospital_id).first()
            if not hospital:
                raise HTTPException(status_code=404, detail="Selected hospital was not found")
        elif payload.hospital_name:
            hospital = db.query(Hospital).filter(Hospital.name.ilike(payload.hospital_name)).first()
            if not hospital:
                hospital = Hospital(
                    name=payload.hospital_name,
                    address=payload.hospital_address,
                    city=payload.hospital_city,
                    country=payload.hospital_country,
                    phone=payload.hospital_phone,
                    email=payload.hospital_email,
                )
                db.add(hospital)
                db.flush()

        user = User(
            full_name=payload.full_name.strip(),
            email=email,
            password_hash=hash_password(payload.password),
            country_code=payload.country_code,
            phone_number=payload.phone_number,
            job_title=payload.job_title,
            role="therapist",
            status="pending",
            email_verified=False,
            hospital_id=hospital.id if hospital else None,
        )
        db.add(user)
        db.flush()

        # Issue the first OTP for email ownership verification. The account
        # cannot log in (see auth/login.py) until this is completed, even if
        # a Super Admin later approves it.
        otp = generate_otp()
        db.add(EmailVerificationToken(
            user_id=user.id,
            email=user.email,
            otp_hash=hash_otp(otp),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=EMAIL_OTP_EXPIRE_MINUTES),
        ))

        db.add(AuditLog(entity_type="User", entity_id=user.id, action="register",
                         performed_by=user.id, details={"email": user.email, "hospital_id": user.hospital_id}))
        db.commit()

        subject, html_body = email_verification_otp_email(user.full_name, otp, EMAIL_OTP_EXPIRE_MINUTES)
        mail_sent = send_mail(user.email, subject, html_body)

        response = {
            "status": "verification_required",
            "message": "Registration submitted. We've emailed you a 6-digit code — verify it to continue. "
                        "Once verified, your account will still need Super Admin approval before you can log in.",
            "user_id": user.id,
            "email": user.email,
        }
        if not mail_sent:
            # Mail failed or isn't configured. In production we deliberately
            # do NOT put the OTP in the response - the account just won't
            # be able to verify until SMTP is fixed, which is loud and
            # visible in the server logs (mail/mailer.py logs a warning),
            # not a silent auth bypass. The dev_otp escape hatch below only
            # ever activates when APP_ENV=development is set on purpose.
            if IS_DEV_ENV:
                response["dev_otp"] = otp
                response["dev_note"] = "APP_ENV=development - email delivery is not configured, so this code is returned directly for local testing only."
            else:
                response["message"] += " (Note: our email service is temporarily unavailable - contact support if you don't receive a code shortly.)"
        return response


@router.get("/hospitals")
def list_hospitals_for_registration(search: str | None = None):
    """Public lookup used by the registration form's hospital autocomplete."""
    with get_db() as db:
        q = db.query(Hospital).filter(Hospital.is_active == True)
        if search:
            q = q.filter(Hospital.name.ilike(f"%{search}%"))
        rows = q.order_by(Hospital.name).limit(20).all()
        return [{"id": h.id, "name": h.name, "city": h.city, "country": h.country} for h in rows]
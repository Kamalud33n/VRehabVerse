import io
import os
import uuid
import datetime

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

from database import get_db
from models import User, Hospital, AuditLog, EmailVerificationToken
from auth.dependencies import get_current_user
from auth.schemas import ProfileUpdateRequest, ChangePasswordRequest, ChangeEmailRequest
from auth.security import verify_password, hash_password, generate_otp, hash_otp, EMAIL_OTP_EXPIRE_MINUTES, IS_DEV_ENV
from auth.email_validation import validate_registration_email
from mail.mailer import send_mail, MAIL_ENABLED
from mail.templates import email_verification_otp_email
from config import UPLOADS_DIR

router = APIRouter(prefix="/api/profile", tags=["profile"])

PROFILE_PICTURES_DIR = os.path.join(UPLOADS_DIR, "profile_pictures")
os.makedirs(PROFILE_PICTURES_DIR, exist_ok=True)

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# Every profile picture is normalized to this final square size before being
# saved, regardless of what the client (crop editor) sends. This guarantees
# a consistent, predictable "profile-picture format" on disk - same
# dimensions, same aspect ratio, same file type - no matter which browser,
# device, or image the user originally uploaded.
PROFILE_PICTURE_SIZE = 480


def _normalize_profile_picture(raw_bytes: bytes) -> bytes:
    """
    Takes whatever image bytes were uploaded (ideally already
    square-cropped by the client-side crop editor) and guarantees a
    consistent output: RGB, square, PROFILE_PICTURE_SIZE x
    PROFILE_PICTURE_SIZE, saved as JPEG.

    The center-crop-to-square step here is a safety net, not the primary
    cropping mechanism - the crop editor already sends a square image in
    the normal flow. This just protects against any non-square image
    still getting through (e.g. a client that skipped the editor) instead
    of silently distorting/stretching it.
    """
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert("RGB")
    except UnidentifiedImageError:
        raise HTTPException(400, "The uploaded file is not a valid image")

    width, height = img.size
    if width != height:
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        img = img.crop((left, top, left + side, top + side))

    img = img.resize((PROFILE_PICTURE_SIZE, PROFILE_PICTURE_SIZE), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _delete_profile_picture_file(relative_path: str | None):
    """Best-effort removal of the on-disk file for a stored profile_picture_path.
    Never raises - a missing/already-gone file should not block the caller
    (upload replacing an old picture, or an explicit delete)."""
    if not relative_path:
        return
    filename = os.path.basename(relative_path)
    file_path = os.path.join(PROFILE_PICTURES_DIR, filename)
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
    except OSError:
        pass


def _profile_out(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "job_title": user.job_title,
        "country_code": user.country_code,
        "phone_number": user.phone_number,
        "profile_picture_path": user.profile_picture_path,
        "hospital": {
            "id": user.hospital.id,
            "name": user.hospital.name,
            "address": user.hospital.address,
            "city": user.hospital.city,
            "country": user.hospital.country,
            "phone": user.hospital.phone,
            "email": user.hospital.email,
            "logo_path": user.hospital.logo_path,
        } if user.hospital else None,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


@router.get("")
def get_my_profile(current_user: User = Depends(get_current_user)):
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user.id).first()
        return _profile_out(user)


@router.put("")
def update_my_profile(payload: ProfileUpdateRequest, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user.id).first()

        if payload.full_name is not None:
            user.full_name = payload.full_name.strip()
        if payload.phone_number is not None:
            user.phone_number = payload.phone_number
        if payload.country_code is not None:
            user.country_code = payload.country_code
        if payload.job_title is not None:
            user.job_title = payload.job_title

        # Hospital details are only editable by the therapist for their
        # OWN linked hospital record - not a permission to reassign hospitals.
        if user.hospital_id and any([
            payload.hospital_name, payload.hospital_address,
            payload.hospital_phone, payload.hospital_email,
        ]):
            hospital = db.query(Hospital).filter(Hospital.id == user.hospital_id).first()
            if hospital:
                if payload.hospital_name is not None:
                    hospital.name = payload.hospital_name
                if payload.hospital_address is not None:
                    hospital.address = payload.hospital_address
                if payload.hospital_phone is not None:
                    hospital.phone = payload.hospital_phone
                if payload.hospital_email is not None:
                    hospital.email = payload.hospital_email

        db.add(AuditLog(entity_type="User", entity_id=user.id, action="profile_updated", performed_by=user.id))
        db.commit()
        db.refresh(user)
        return _profile_out(user)


@router.post("/picture")
async def upload_profile_picture(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(400, "Only jpg, jpeg, png, or webp images are allowed")

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 5MB")

    # Normalize to a fixed-size square JPEG regardless of what was
    # uploaded (see _normalize_profile_picture). Output is always .jpg,
    # so the on-disk extension no longer depends on the source file type.
    normalized_bytes = _normalize_profile_picture(contents)

    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}.jpg"
    dest_path = os.path.join(PROFILE_PICTURES_DIR, filename)

    with open(dest_path, "wb") as f:
        f.write(normalized_bytes)

    relative_path = f"/uploads/profile_pictures/{filename}"
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user.id).first()
        old_path = user.profile_picture_path
        user.profile_picture_path = relative_path
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="profile_picture_updated", performed_by=user.id))
        db.commit()

    # Replacing an existing picture - clean up the old file now that the
    # new one is safely saved and committed. Deleting an already-gone file
    # is a no-op (best-effort), so this is safe even if it was already removed.
    if old_path and old_path != relative_path:
        _delete_profile_picture_file(old_path)

    return {"profile_picture_path": relative_path}


@router.delete("/picture")
def delete_profile_picture(current_user: User = Depends(get_current_user)):
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user.id).first()
        old_path = user.profile_picture_path
        if not old_path:
            return {"profile_picture_path": None}

        user.profile_picture_path = None
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="profile_picture_removed", performed_by=user.id))
        db.commit()

    _delete_profile_picture_file(old_path)
    return {"profile_picture_path": None}


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user.id).first()
        if not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(400, "Current password is incorrect")

        user.password_hash = hash_password(payload.new_password)
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="password_changed", performed_by=user.id))
        db.commit()
        return {"status": "ok"}


@router.post("/change-email")
def change_email(payload: ChangeEmailRequest, current_user: User = Depends(get_current_user)):
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user.id).first()
        if not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(400, "Current password is incorrect")

        # Same backend checks as registration — a client can't smuggle a
        # fake/disposable/non-existent-domain address past this either.
        new_email = validate_registration_email(payload.new_email)
        if new_email == user.email:
            raise HTTPException(400, "This is already your current email")

        clash = db.query(User).filter(User.email == new_email, User.id != user.id).first()
        if clash:
            raise HTTPException(400, "An account with this email already exists")

        old_email = user.email
        user.email = new_email
        # Changing the email re-opens the ownership question, so it must be
        # re-verified before the account can be used again — otherwise a
        # therapist could verify a real address once, get approved, then
        # swap to any unverified/fake address and stay logged in.
        user.email_verified = False
        user.email_verified_at = None
        db.add(AuditLog(entity_type="User", entity_id=user.id, action="email_changed", performed_by=user.id,
                         details={"old_email": old_email, "new_email": new_email}))

        otp = generate_otp()
        db.add(EmailVerificationToken(
            user_id=user.id,
            email=new_email,
            otp_hash=hash_otp(otp),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=EMAIL_OTP_EXPIRE_MINUTES),
        ))
        db.commit()
        db.refresh(user)

        subject, html_body = email_verification_otp_email(user.full_name, otp, EMAIL_OTP_EXPIRE_MINUTES)
        mail_sent = send_mail(new_email, subject, html_body)

        out = _profile_out(user)
        out["verification_required"] = True
        out["message"] = "Email updated. We've sent a verification code to your new address — verify it to keep using your account."
        # Same gate as register.py / verify_email.py / forgot_password.py:
        # only leak the OTP in the response when APP_ENV=development is
        # set on purpose. Previously this checked `not MAIL_ENABLED or not
        # mail_sent` with no IS_DEV_ENV check, so a mail outage in
        # production would hand out the OTP to whoever hit this endpoint.
        if (not MAIL_ENABLED or not mail_sent) and IS_DEV_ENV:
            out["dev_otp"] = otp
            out["dev_note"] = "APP_ENV=development - email delivery is not configured, so this code is returned directly for local testing only."
        return out
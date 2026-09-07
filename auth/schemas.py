import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    country_code: str = Field(min_length=2, max_length=10)
    phone_number: str = Field(min_length=4, max_length=30)
    password: str = Field(min_length=8)
    confirm_password: str
    job_title: str | None = None

    # Hospital — either link to an existing hospital by id, or register a
    # new one inline (name required in that case).
    hospital_id: str | None = None
    hospital_name: str | None = None
    hospital_address: str | None = None
    hospital_city: str | None = None
    hospital_country: str | None = None
    hospital_phone: str | None = None
    hospital_email: str | None = None

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, info):
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Password and confirm password do not match")
        return v


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=4, max_length=8)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, info):
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v


class UserOut(BaseModel):
    id: str
    full_name: str
    email: str
    email_verified: bool
    role: str
    status: str
    job_title: str | None = None
    country_code: str | None = None
    phone_number: str | None = None
    profile_picture_path: str | None = None
    hospital_id: str | None = None
    hospital_name: str | None = None
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None
    country_code: str | None = None
    job_title: str | None = None
    hospital_name: str | None = None
    hospital_address: str | None = None
    hospital_phone: str | None = None
    hospital_email: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    current_password: str
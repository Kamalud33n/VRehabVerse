"""
Extra backend-side email checks used at registration time, on top of the
basic RFC-format validation Pydantic's EmailStr already does.

Two independent checks:
  1. Disposable/temp-mail domain blocklist — catches the "obviously fake,
     throwaway" case (mailinator.com, 10minutemail.com, etc).
  2. DNS deliverability check (MX/A record lookup) — catches the "made up,
     non-existent domain" case (e.g. someone@asdkjhasd123.com).

Neither of these proves a *specific person* owns the mailbox — that's what
the OTP verification step (auth/register.py + auth/verify_email.py) is for.
These just stop obviously-junk addresses before an OTP email is ever sent.
"""
from email_validator import validate_email, EmailNotValidError
from fastapi import HTTPException

# Small, commonly-abused set of disposable/temporary email providers.
# Not exhaustive — the DNS deliverability check + OTP ownership proof are
# the real backstops. This just short-circuits the obvious cases quickly.
DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "sharklasers.com",
    "10minutemail.com", "10minutemail.net", "20minutemail.com", "temp-mail.org",
    "tempmail.com", "tempmail.net", "throwawaymail.com", "yopmail.com", "yopmail.net",
    "getnada.com", "trashmail.com", "trashmail.net", "fakeinbox.com", "dispostable.com",
    "mailnesia.com", "mintemail.com", "mytemp.email", "moakt.com", "maildrop.cc",
    "spamgourmet.com", "mailcatch.com", "emailondeck.com", "discard.email",
    "example.com", "example.org", "example.net", "test.com", "invalid.com",
}


def validate_registration_email(raw_email: str) -> str:
    """
    Validates that `raw_email` is a real, well-formed, non-disposable
    address with a domain capable of receiving mail. Returns the
    normalized (lowercased) address on success.

    Raises HTTPException(422) with a user-facing message on failure.
    Never raises for transient DNS/network errors — those fail *open*
    (registration proceeds; the OTP step still confirms real ownership)
    so a flaky resolver doesn't lock legitimate therapists out.
    """
    email = raw_email.strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""

    if domain in DISPOSABLE_EMAIL_DOMAINS:
        raise HTTPException(
            status_code=422,
            detail="Temporary or disposable email addresses aren't allowed. Please register with your real work or personal email.",
        )

    try:
        result = validate_email(email, check_deliverability=True)
        return result.normalized.lower()
    except EmailNotValidError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"That email address doesn't look valid or reachable: {exc}",
        )
    except Exception:
        # DNS lookup failed for an infrastructure reason (timeout, resolver
        # down, offline dev/sandbox environment, etc) rather than because
        # the address is bad — don't block registration on that; the OTP
        # step still guarantees ownership before the account can log in.
        return email

"""
Creates (or updates) the platform's Super Admin account.

Super admin accounts can't be made through /auth/register (that always
creates role="therapist"), so this script is the one-time bootstrap.
Safe to re-run: if the account already exists it just prints its info
instead of duplicating it.

Usage:
    python create_super_admin.py
    SUPER_ADMIN_EMAIL=you@mednovacare.com SUPER_ADMIN_PASSWORD='Str0ngPass!' python create_super_admin.py

Defaults (if env vars are not set) — CHANGE THESE after first login via
the Profile page:
    email:    admin@mednovacare.com
    password: ChangeMe@123

In production (APP_ENV=production), the default password is refused —
you must pass SUPER_ADMIN_PASSWORD explicitly. This prevents a well-known
admin/password combo from ever being live on a real deployment.
"""
import os
import sys
import datetime

from database import get_db, init_db
from models import User
from auth.security import hash_password, IS_DEV_ENV

DEFAULT_EMAIL = "admin@mednovacare.com"
DEFAULT_PASSWORD = "ChangeMe@123"


def main():
    email = os.getenv("SUPER_ADMIN_EMAIL", DEFAULT_EMAIL).strip().lower()
    password = os.getenv("SUPER_ADMIN_PASSWORD")
    full_name = os.getenv("SUPER_ADMIN_NAME", "Super Admin")

    if not password:
        if IS_DEV_ENV:
            password = DEFAULT_PASSWORD
            print("[create_super_admin] WARNING: SUPER_ADMIN_PASSWORD not set - using "
                  "the default dev password because APP_ENV=development.")
        else:
            print("[create_super_admin] Refusing to create a super admin with the "
                  "well-known default password on a non-development environment "
                  f"(APP_ENV='{os.getenv('APP_ENV', 'production')}').")
            print("[create_super_admin] Set SUPER_ADMIN_PASSWORD to a strong password "
                  "and re-run, e.g.:")
            print("    SUPER_ADMIN_EMAIL=you@mednovacare.com "
                  "SUPER_ADMIN_PASSWORD='Str0ngPass!' python create_super_admin.py")
            sys.exit(1)

    init_db()

    with get_db() as db:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"[create_super_admin] A user with email '{email}' already exists "
                  f"(role={existing.role}). Not touching it.")
            print("[create_super_admin] To change super admin credentials, log in "
                  "and use the Profile page instead of re-running this script.")
            sys.exit(0)

        admin = User(
            full_name=full_name,
            email=email,
            password_hash=hash_password(password),
            role="super_admin",
            status="approved",
            is_active=True,
            email_verified=True,   # bootstrapped by whoever has server access — trusted by definition
            email_verified_at=datetime.datetime.utcnow(),
        )
        db.add(admin)
        db.commit()

        print("[create_super_admin] Super admin created successfully:")
        print(f"    email:    {email}")
        print(f"    password: {password}")
        print("[create_super_admin] Login at /login, then go to Profile to change "
              "the email/password.")


if __name__ == "__main__":
    main()
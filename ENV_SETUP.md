# `.env` Setup Guide — MedNova VR Rehab Backend

`.env` is **never committed** (see `.gitignore`) — every dev/server sets up
their own copy from `env.example`.

```bash
cp env.example .env
```

Then fill in the values below. Grouped by what breaks if you skip them.

---

## 1. Required — app won't start / core features broken without these

| Variable | What it's for |
|---|---|
| `DATABASE_URL` | SQLAlchemy connection string. Local dev (MySQL on your machine): `mysql+pymysql://root:<password>@localhost:3306/mednova_vr_db?charset=utf8mb4`. Docker: leave as-is in `.env`, `docker-compose.yml` overrides the host to `db` automatically. |
| `JWT_SECRET_KEY` | Signs login session tokens. **App refuses to start without this unless `APP_ENV=development`.** Generate a real one with: `python -c "import secrets; print(secrets.token_urlsafe(48))"` — never reuse a key that's appeared in a chat, ticket, or shared doc. |
| `APP_ENV` | `development` or `production`. Controls: whether OTP codes / reset tokens leak into API responses when email isn't configured (dev only), and whether login cookies get the `secure` flag (production only). **Set to `production` before deploying — this is the single most important flag in this file.** |

---

## 2. Required for email to work (OTP verification, password reset)

| Variable | What it's for |
|---|---|
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | e.g. `587` |
| `SMTP_USER` | Full email address used to send |
| `SMTP_PASS` | **App password, not your real password** (Gmail: turn on 2-Step Verification → generate one at https://myaccount.google.com/apppasswords) |
| `MAIL_FROM` | Sender address shown to recipients (defaults to `SMTP_USER` if unset) |
| `MAIL_FROM_NAME` | Sender display name (default: `MedNova VR`) |

If SMTP isn't configured, the app doesn't crash — it just can't send OTP/reset
emails. In `APP_ENV=development` only, the OTP/reset token gets returned
directly in the API response instead, so you can still test locally without
real SMTP. In production this never happens.

---

## 3. Required in production (safe defaults for local dev)

| Variable | Local dev default | Production |
|---|---|---|
| `ALLOWED_ORIGINS` | `http://localhost:8000` | Comma-separated real frontend/VR domain(s), e.g. `https://vr-backend.ourdomain.com` |
| `APP_BASE_URL` | anything (only used in emailed links) | Your real public URL — this is what OTP/reset emails link back to |
| `DB_ROOT_PASSWORD` | `changeme` | A real MySQL root password — **only read by `docker-compose.yml`**, not the app itself |

---

## 4. Optional — sensible defaults, override only if you need to

| Variable | Default | What it changes |
|---|---|---|
| `SEED_DEMO_DATA` | `false` | `true` seeds 2 demo patients on first startup. Dev only — keep `false` in production. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | How long a login session lasts |
| `RESET_TOKEN_EXPIRE_MINUTES` | `30` | How long a password-reset link stays valid |
| `EMAIL_OTP_EXPIRE_MINUTES` | `15` | How long an email-verification OTP stays valid |
| `EMAIL_OTP_MAX_ATTEMPTS` | `5` | Wrong-OTP attempts allowed per code before it's locked |
| `EMAIL_OTP_RESEND_COOLDOWN_SECONDS` | `60` | Minimum gap between two OTP resend requests |

### Rate limiting (added with the brute-force fix)

| Variable | Default | Caps |
|---|---|---|
| `LOGIN_RATE_LIMIT` | `8/minute` | `/auth/login` per IP |
| `OTP_VERIFY_RATE_LIMIT` | `10/minute` | `/auth/verify-email` per IP |
| `OTP_RESEND_RATE_LIMIT` | `5/hour` | `/auth/resend-verification-email` per IP |
| `FORGOT_PASSWORD_RATE_LIMIT` | `5/hour` | `/auth/forgot-password` per IP |
| `RESET_PASSWORD_RATE_LIMIT` | `10/hour` | `/auth/reset-password` per IP |

Format is any string `limits`/`slowapi` accepts: `"<number>/<second|minute|hour|day>"`.

---

## 5. Example — local development `.env`

```env
DATABASE_URL=mysql+pymysql://root:mypassword@localhost:3306/mednova_vr_db?charset=utf8mb4
SEED_DEMO_DATA=true
APP_ENV=development
JWT_SECRET_KEY=dev-only-any-random-string-here

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=youraddress@gmail.com
SMTP_PASS=your16charapppassword
MAIL_FROM=youraddress@gmail.com
MAIL_FROM_NAME=MedNova VR

ALLOWED_ORIGINS=http://localhost:8000
APP_BASE_URL=http://localhost:8000
```

## 6. Example — production `.env`

```env
DATABASE_URL=mysql+pymysql://root:<real-password>@db:3306/mednova_vr_db?charset=utf8mb4
DB_ROOT_PASSWORD=<real-mysql-root-password>
SEED_DEMO_DATA=false
APP_ENV=production
JWT_SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_urlsafe(48))">

SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=noreply@mednovacare.com
SMTP_PASS=<real-smtp-password>
MAIL_FROM=noreply@mednovacare.com
MAIL_FROM_NAME=MedNova VR

ALLOWED_ORIGINS=https://vr-backend.mednovacare.com
APP_BASE_URL=https://vr-backend.mednovacare.com
```

---

## Notes

- **`alembic.ini` is separate from `.env`.** Its `sqlalchemy.url` line is read
  directly by `migrations/env.py` and is *not* wired to `DATABASE_URL` — fill
  in your local MySQL password there too before running `alembic upgrade
  head`, but never commit a real password in that file either.
- Docker: `docker-compose.yml` loads every variable in `.env` via `env_file`,
  then overrides just `DATABASE_URL`'s host to `db` (the MySQL service name
  inside the compose network) — everything else in `.env` reaches the
  container as-is.

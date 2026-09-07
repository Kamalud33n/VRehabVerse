# MedNova VR Rehabilitation Platform — Backend

A FastAPI backend powering a Meta Quest VR physiotherapy game, with a real-time
clinician-facing dashboard for monitoring sessions, tracking patient progress,
and generating clinical PDF reports.

---

## 1. What this platform does

Patients perform guided rehab exercises inside a VR headset (a separate Unity
app maintained by the VR team). The headset streams live session data — reps,
range of motion (ROM), accuracy, and camera POV — to this backend over
WebSockets. Therapists and admins watch progress in real time on a web
dashboard and can generate PDF clinical reports after each session.

**Tech stack**

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (Python) |
| Server | Uvicorn (ASGI) |
| Database | MySQL 8.0 (SQLAlchemy ORM) |
| Real-time | WebSockets (native FastAPI) |
| Auth | Cookie-based JWT (HS256) + bcrypt password hashing |
| Templates | Jinja2 |
| PDF reports | ReportLab |
| Frontend | Vanilla JS + Chart.js |
| Containerization | Docker / docker-compose |
| Rate limiting | slowapi (per-IP, in-memory) on auth endpoints |

---

## 2. Who uses it, and how they access it

There are two roles:

| Role | How the account is created | What they can do |
|---|---|---|
| `super_admin` | Bootstrapped once via `create_super_admin.py` (see Setup) | Full platform oversight: approve/reject therapist registrations, view all patients across all therapists, manage customers, handle support complaints |
| `therapist` | Self-registers at `/register`, then needs super-admin approval before they can log in | Manage only their own patients, run VR sessions, view their own analytics and reports |

**Access flow for a therapist:**
1. Register at `/register`
2. Verify email via a 6-digit OTP sent by email (`/verify-email`)
3. Wait for a super admin to approve the account (`status: pending → approved`)
4. Log in at `/login` → redirected to `/dashboard`

**Access flow for the super admin:**
1. Log in at `/login` with the account created by `create_super_admin.py`
2. Lands on `/admin/dashboard` — from there: `/admin/registrations` (approve
   therapists), `/admin/customers`, `/admin/support`, `/admin/profile`

**Page routes** (HTML, session-cookie protected where noted):

| Route | Who | Purpose |
|---|---|---|
| `/`, `/login`, `/register`, `/forgot-password`, `/reset-password` | anyone | Public auth pages |
| `/dashboard` | logged in | Home dashboard |
| `/patients` | logged in | Patient list/management |
| `/session` | logged in | Live VR session view |
| `/reports` | logged in | Generate/view PDF reports |
| `/analytics` | logged in | Trends across sessions |
| `/settings`, `/profile` | logged in | Account settings |
| `/admin/*` | super_admin only | Admin dashboard, registrations, customers, support |

Authentication is a JWT stored in an `httponly` cookie, issued at `/auth/login`
and required (via the `get_current_user`/`require_roles` dependencies) on
every protected API route.

---

## 3. Project structure

```
vr1/
├── app.py                     # FastAPI app entrypoint, router registration, CORS, health check
├── config.py                  # PDF colors, Jinja2 template setup, folder paths
├── database.py                # DB engine/session setup, init_db(), auto column sync
├── models.py                  # SQLAlchemy models (User, Hospital, Patient, Session, Report, ...)
├── create_super_admin.py      # One-time bootstrap script for the first admin account
├── cleanup_orphan_patients.py # Maintenance script
├── requirements.txt
├── docker-compose.yml         # web + MySQL services
├── Dockerfile
├── .env                       # local/production secrets (NOT committed)
├── .gitignore
├── env.example                # template for required env vars
│
├── auth/
│   ├── login.py, register.py            # session issuing, account creation
│   ├── verify_email.py                  # OTP email verification
│   ├── forgot_password.py, reset_password.py
│   ├── rate_limit.py                    # slowapi limiter + per-route rate-limit tunables
│   ├── security.py                      # JWT, password hashing, OTP helpers, APP_ENV logic
│   ├── dependencies.py                  # get_current_user, require_roles
│   └── schemas.py, email_validation.py
│
├── routers/
│   ├── pages.py                # HTML page routes
│   ├── ws.py                   # WebSocket endpoints: /ws/vr, /ws/dashboard
│   ├── clinical/
│   │   ├── patients.py          # patient CRUD (tenant-scoped per therapist)
│   │   ├── sessions.py          # session lifecycle, session codes
│   │   ├── dashboard.py         # dashboard data endpoints
│   │   ├── analytics.py         # analytics/aggregation endpoints
│   │   ├── reports.py           # PDF report generation endpoints
│   │   └── export.py            # data export endpoints
│   └── account/
│       ├── admin.py             # super-admin oversight endpoints
│       ├── profile.py           # profile picture, password/email change
│       └── support.py           # complaints/support tickets
│
├── services/
│   ├── vr_ws_manager.py         # VR + dashboard WebSocket connection managers
│   ├── report_builder.py        # ReportLab PDF report generation
│   ├── game_metrics.py          # metric calculations
│   └── helpers.py
│
├── mail/                       # SMTP mailer + email templates (OTP, reset, notifications)
├── migrations/                 # Alembic migrations
├── templates/                  # Jinja2 HTML (auth/, app/, admin/)
├── static/                     # JS, CSS, Chart.js
├── assets/                     # logo, static assets
├── uploads/                    # profile pictures etc. (persisted volume)
├── reports/                    # generated PDF reports (persisted volume)
└── data/                       # misc app data (persisted volume)
```

---

## 4. WebSocket endpoints

| Endpoint | Purpose |
|---|---|
| `/ws/vr` | VR headset → backend: live session metrics (reps, ROM, accuracy, heartbeat, camera frames) |
| `/ws/dashboard` | Backend → clinician dashboard: real-time push updates when sessions start/update/end |

Both automatically use `wss://` when the page is served over HTTPS — no code
changes needed when moving between environments.

---

## 5. Core features

- **Session codes** — a therapist generates a short-lived code (default 15 min
  TTL) that links a VR headset to a specific patient session.
- **Live metrics** — reps, ROM %, and accuracy pushed in real time via
  WebSocket, with a heartbeat interval (default 10s).
- **VR camera POV streaming** — JPEG frames from the headset relayed to the
  dashboard for live supervision.
- **Therapist-controlled session flow** — sessions only start timing once the
  therapist explicitly begins them, for accurate duration tracking.
- **PDF clinical reports** — auto-generated per session/date range, including
  KPI cards, progress charts, and a clinical summary.
- **Analytics dashboard** — trends across sessions per patient (ROM, accuracy,
  session count).
- **Role-based access** — therapists only ever see their own patients; super
  admins get full oversight plus a registration-approval workflow.
- **Email verification + password reset** — OTP-based, HMAC-hashed codes,
  never stored or logged in plaintext.
- **Rate-limited auth endpoints** — `/auth/login`, `/auth/verify-email`,
  `/auth/resend-verification-email`, `/auth/forgot-password`, and
  `/auth/reset-password` are all capped per-IP (via `slowapi`) to close off
  brute-force password/OTP guessing and email-spam abuse. Limits are
  env-tunable — see `auth/rate_limit.py` and section 8.

---

## 6. Setup — Local development

### 6.1 Clone & install dependencies

```bash
git clone <repo-url>
cd vr1
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 6.2 Configure environment

Copy `env.example` to `.env` and fill in local values:

```bash
cp env.example .env
```

Minimum for local dev:
```env
DATABASE_URL=mysql+pymysql://root:<password>@localhost:3306/mednova_vr_db?charset=utf8mb4
SEED_DEMO_DATA=false
APP_ENV=development
JWT_SECRET_KEY=<any-random-string-for-local-dev>
```

### 6.3 Run MySQL

Either run a local MySQL 8.0 instance (the app auto-creates the
`mednova_vr_db` database and tables on first startup), or use the bundled
Docker service — see section 7.

### 6.4 Start the server

```bash
python app.py
```

The API is live at `http://localhost:8000`. Health check: `GET /api/health`.

### 6.5 Create the first admin account

```bash
SUPER_ADMIN_EMAIL=you@mednovacare.com SUPER_ADMIN_PASSWORD='YourStrongPassword!' python create_super_admin.py
```

Log in at `/login` with those credentials.

---

## 7. Setup — Docker

```bash
docker-compose up --build
```

This spins up:
- `db` — MySQL 8.0 with a persistent volume
- `web` — the FastAPI app on port `8000`, loaded with every variable from
  `.env` via `env_file` (so `APP_ENV`, `JWT_SECRET_KEY`, SMTP settings, etc.
  all reach the container — not just `DATABASE_URL`)

`uploads/`, `reports/`, and `data/` are mounted as host volumes so generated
files survive container restarts.

Then create the first admin **inside** the running container:

```bash
docker-compose exec web env SUPER_ADMIN_EMAIL=you@mednovacare.com SUPER_ADMIN_PASSWORD='YourStrongPassword!' python create_super_admin.py
```

---

## 8. Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | SQLAlchemy MySQL connection string |
| `JWT_SECRET_KEY` | ✅ in production | Signs login tokens. The app refuses to start without one unless `APP_ENV=development`. |
| `APP_ENV` | ✅ | `production` or `development`. Controls cookie security and whether OTP/reset codes leak into API responses — see section 9. |
| `SEED_DEMO_DATA` | optional | `true` to seed demo patients on first startup (dev only — keep `false` in production) |
| `DB_ROOT_PASSWORD` | Docker only | MySQL root password used by `docker-compose.yml` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | ✅ for email | Used for OTP verification, password reset, and notification emails |
| `MAIL_FROM` / `MAIL_FROM_NAME` | optional | Sender identity on outgoing email |
| `ALLOWED_ORIGINS` | ✅ in production | Comma-separated list of domains allowed to call the API (CORS) |
| `APP_BASE_URL` | ✅ in production | Public URL used in emailed links |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | optional | Login session length (default 60) |
| `LOGIN_RATE_LIMIT` | optional | Per-IP cap on `/auth/login` (default `8/minute`) |
| `OTP_VERIFY_RATE_LIMIT` | optional | Per-IP cap on `/auth/verify-email` (default `10/minute`) |
| `OTP_RESEND_RATE_LIMIT` | optional | Per-IP cap on `/auth/resend-verification-email` (default `5/hour`) |
| `FORGOT_PASSWORD_RATE_LIMIT` | optional | Per-IP cap on `/auth/forgot-password` (default `5/hour`) |
| `RESET_PASSWORD_RATE_LIMIT` | optional | Per-IP cap on `/auth/reset-password` (default `10/hour`) |

Never commit `.env` — it contains live database, email, and signing
credentials. Use `env.example` as the template.

---

## 9. Production checklist before deploying

- [ ] `APP_ENV=production` in `.env` (this is critical — in `development`
      mode, OTP codes and password-reset tokens are returned directly in
      API responses instead of only being emailed, and login cookies skip
      the `secure` flag)
- [ ] `JWT_SECRET_KEY` set to a freshly generated random value — never reuse
      a key that has ever appeared in a chat, ticket, or shared doc
- [ ] `docker-compose.yml`'s `web` service has `env_file: .env` (otherwise
      only `DATABASE_URL` reaches the container and the app will fail to
      start in production mode)
- [ ] `DB_ROOT_PASSWORD`, `SMTP_PASS` rotated to values that have never been
      shared outside the team
- [ ] `alembic.ini`'s `sqlalchemy.url` is a local-only placeholder
      (`<your-local-mysql-password>`) — it is read directly by
      `migrations/env.py` and is separate from the app's own
      `DATABASE_URL`. Fill in your real local password when running
      Alembic commands, but never commit that file with a real
      password in it
- [ ] `ALLOWED_ORIGINS` / `APP_BASE_URL` point at the real production domain,
      not a placeholder
- [ ] `create_super_admin.py` run with an explicit `SUPER_ADMIN_PASSWORD` —
      it refuses to fall back to the default password when `APP_ENV` isn't
      `development`
- [ ] `requirements.txt` version-pinned before building the production image
      (currently unpinned — a fresh install could pull breaking versions)
- [ ] `reports/` and `uploads/` may contain real patient data — already
      excluded from the Docker build via `.dockerignore`; back them up
      separately on the server, not in git
- [ ] Auth rate limits (`auth/rate_limit.py`) use `slowapi`'s in-memory
      storage, which is per-process — correct for the current single-`web`-
      container deployment. If this ever scales to multiple app instances
      behind a load balancer, point the limiter at a shared backend (e.g.
      `Limiter(storage_uri="redis://...")`) or each instance will enforce
      the limit independently, multiplying the effective cap

---

## 10. Maintainer

Built and maintained solo by Kamaludeen — AI/ML & Backend Engineer, MedNova
(Muscat, Oman).
# Production Readiness Checklist — MedNova VR Rehab Backend

Everything here is either an env/config value (see `ENV_SETUP.md` for the
`.env` side of it) or a one-time operational step. Nothing in this file is
packaged automatically — each box is something someone has to actually do
on the target server.

---

## A. Environment & secrets

- [ ] `.env` created on the server from `env.example`, filled with real
      values — see `ENV_SETUP.md`
- [ ] `APP_ENV=production` — **the single most important flag.** In
      `development` mode, OTP codes and password-reset tokens are returned
      directly in API responses instead of only being emailed, and login
      cookies skip the `secure` flag
- [ ] `JWT_SECRET_KEY` freshly generated (`python -c "import secrets;
      print(secrets.token_urlsafe(48))"`) — never reuse a key that's
      appeared in a chat, ticket, or shared doc
- [ ] `DB_ROOT_PASSWORD`, `SMTP_PASS` rotated to values that have never been
      shared outside the team
- [ ] `ALLOWED_ORIGINS` / `APP_BASE_URL` point at the real production
      domain, not `localhost`
- [ ] `alembic.ini`'s `sqlalchemy.url` filled in locally with the real DB
      password when running migrations — it's read directly by
      `migrations/env.py`, separate from `.env`'s `DATABASE_URL`. Never
      commit that file with a real password in it
- [ ] `docker-compose.yml`'s `web` service still has `env_file: .env`
      (otherwise only `DATABASE_URL` reaches the container and the app
      fails to start in production mode)

## B. One-time CLI steps

- [ ] First super-admin account created **with an explicit password** —
      the script refuses to fall back to the default password once
      `APP_ENV` isn't `development`:
      ```bash
      # bare metal
      SUPER_ADMIN_EMAIL=you@mednovacare.com SUPER_ADMIN_PASSWORD='<strong-password>' python create_super_admin.py

      # docker
      docker-compose exec web env SUPER_ADMIN_EMAIL=you@mednovacare.com SUPER_ADMIN_PASSWORD='<strong-password>' python create_super_admin.py
      ```
- [ ] `requirements.txt` version-pinned before building the production
      image (currently unpinned — a fresh `pip install` could pull a
      breaking version of any dependency). Generate a pinned copy from a
      working install: `pip freeze > requirements.lock.txt`, review it,
      then build the image from that.

## C. Network / TLS

The app itself only ever serves plain HTTP (`uvicorn` on port `8000`) — it
does not terminate TLS. `secure=not IS_DEV_ENV` on the login cookie
(`auth/login.py`) assumes HTTPS is reaching the browser some other way.

- [ ] A reverse proxy (nginx, Caddy, or your cloud load balancer) sits in
      front of the `web` container, terminates HTTPS, and forwards to
      `localhost:8000` / the container's port
- [ ] A real TLS certificate is installed on that proxy (Let's Encrypt via
      Caddy/certbot, or your cloud provider's managed cert)
- [ ] `ALLOWED_ORIGINS` and `APP_BASE_URL` use `https://`, matching what's
      actually reaching users — if these still say `http://`, the secure
      cookie won't be sent back by the browser and login will silently
      fail over HTTPS
- [ ] The reverse proxy passes `X-Forwarded-For` / `X-Forwarded-Proto`
      correctly if you want accurate client IPs in logs and in the
      `slowapi` rate limiter (`auth/rate_limit.py` keys off
      `get_remote_address`, which reads the connecting IP — behind a proxy
      that's the proxy's IP unless forwarded headers are trusted)

## D. Data & backups

`reports/`, `uploads/`, and `data/` are mounted as host volumes
(`docker-compose.yml`) so they survive container restarts — but a restart
isn't the same as a backup.

- [ ] `reports/` and `uploads/` may contain real patient data — confirmed
      excluded from git (`.gitignore`) and the Docker build
      (`.dockerignore`); they still live only on the host's local disk
- [ ] A backup job exists for the MySQL `db_data` volume (e.g. scheduled
      `mysqldump` to off-host storage) — nothing in this repo does this
      automatically
- [ ] A backup job exists for `uploads/` and `reports/` themselves (e.g.
      rsync/cron to off-host storage, or point them at a mounted network
      volume instead of local disk)
- [ ] Restore procedure has actually been tested at least once, not just
      assumed to work

## E. Scaling (only if you ever run more than one `web` instance)

- [ ] If you move from the current single-`web`-container setup
      (`docker-compose.yml`) to multiple instances behind a load balancer,
      the auth rate limiter (`auth/rate_limit.py`) needs to move off its
      default in-memory storage to a shared backend, e.g.
      `Limiter(storage_uri="redis://...")` — otherwise each instance
      enforces the limit independently and the effective cap multiplies by
      instance count

---

Once A–D are done, the app is reasonably production-ready for a
single-server deployment. E only matters if/when you scale out.

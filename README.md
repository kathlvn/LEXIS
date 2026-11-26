# LEXIS — Legal Executive Information System

A web system for the Legal Office of Eastern Samar State University (ESSU).
Clients send and follow legal service requests online; Legal Office staff
process them in an internal console. Built as an undergraduate (BSCS) thesis
project. The Legal Office's Operations Manual and Citizen's Charter set how
the system works.

> Status: thesis prototype, not yet deployed. Pages marked "draft" (About,
> Privacy Notice, Terms of Use, Cookies) still await the Legal Office's review.

## What it does

**Clients (public site)**
- Sign in with an email address and a one-time code: no passwords.
- Request one of six services (Legal Advice, Legal Research, Review of
  MOA/MOU and Contracts, Conduct of Investigation, Drafting of Legal
  Documents, Other Legal Concerns) or file a complaint/referral.
- Follow each request: status, documents, history, and messages with the office.
- Answer a short satisfaction survey (CSM) once a request is completed.

**Legal Office staff (console)**
- Queues (new, assigned, awaiting client, for approval, completed) and a dashboard.
- Request Detail: processing steps, attorney review and approval, returns,
  documents, messages, and client verification.
- Walk-in requests encoded by staff, including clients without email.
- Due dates counted in working days (weekends and holidays skipped), with an
  importer that reads the yearly holiday proclamation from the Official Gazette.
- Reports, Records Archive, Security Log, and Head Attorney settings
  (staff accounts, services, offices, holidays, limits).
- Articles for the public Home page.

## Stack

| Part | What |
|---|---|
| Backend | Python, FastAPI + SQLModel (`backend/`) |
| Database | PostgreSQL 16 (Docker for development) |
| Frontend | Plain HTML, CSS and JavaScript, no framework or build step (`interface/`) |
| Web server | Caddy: serves `interface/` and proxies `/api/*` to the backend on one address |
| Email | SMTP |
| Files | Local disk in development; Google Drive (Shared Drive) as the planned store |
| Holiday import | Scrapling (plain HTTP) + Tesseract OCR |

## Run it locally

You need Python 3.13, Docker, [Caddy](https://caddyserver.com/), and (for the
holiday importer only) [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki).

1. **Settings.** Copy `backend/.env.example` to `backend/.env` and fill in
   `DB_PASSWORD`, `SECRET_KEY` and the same password inside `DATABASE_URL`.
   Leave `SMTP_HOST` empty to send no email; in development, sign-in codes
   are printed to the backend's console instead.
2. **Database.** From `backend/`:
   ```sh
   docker compose --env-file .env up -d db
   ```
3. **Python environment.** From the repository root:
   ```sh
   python -m venv .venv
   .venv/Scripts/pip install -r backend/requirements.txt     # Windows
   # .venv/bin/pip install -r backend/requirements.txt       # macOS / Linux
   ```
4. **Backend.** From `backend/` (creates the tables and default data on start):
   ```sh
   ../.venv/Scripts/uvicorn app.main:app --reload
   ```
5. **First staff account.** From `backend/`, with
   `BOOTSTRAP_HEAD_ATTORNEY_NAME`, `BOOTSTRAP_HEAD_ATTORNEY_EMAIL` and
   `BOOTSTRAP_HEAD_ATTORNEY_PASSWORD` set in the environment:
   ```sh
   ../.venv/Scripts/python -m app.bootstrap_head_attorney
   ```
6. **Web server.** From the repository root:
   ```sh
   caddy run --config Caddyfile
   ```
   Public site: <http://localhost:8080/home.html> · Staff console:
   <http://localhost:8080/internal/login.html>

Deployment on a server is described in [docs/deployment.md](docs/deployment.md),
and its risks in [docs/risks.md](docs/risks.md).

## Tests

Plain Python scripts (no pytest), run one at a time from `backend/`. Tests
that need a database use a throwaway SQLite one, never the real database:

```sh
../.venv/Scripts/python tests/test_role_matrix.py
```

`tests/test_holiday_import.py` and the live part of
`tests/test_holiday_suggestions.py` go online (Official Gazette) and need Tesseract.

## Project layout

```
backend/
  app/
    main.py          app, routers, start-up checks
    models/          database tables
    schemas/         request/response shapes
    routers/         API endpoints (portal, requests, documents, reports, ...)
    core/            security, sessions, email, storage, working days, holiday import
    seed.py          default services and settings
    csm_form.json    the satisfaction survey questions (editable)
  tests/
interface/
  *.html, js/, css/  public site
  internal/          staff console
  fonts/             self-hosted fonts (SIL Open Font License)
docs/                deployment guide and risks
Caddyfile            web server, security headers, Content-Security-Policy
```

## Security and privacy

- Client sign-in by one-time email code; staff passwords hashed with Argon2id;
  idle and maximum session times; account lockout and rate limits.
- Roles are checked on the server for every endpoint, not only hidden in the page.
- A strict Content-Security-Policy: no inline scripts, no third-party scripts,
  fonts, trackers or analytics (RA 10173, the Data Privacy Act of 2012).
- Uploaded files are checked by content, kept outside the web folder, and
  never reachable by a guessable link.
- Never commit `backend/.env`, uploaded files, or service-account keys; they
  are listed in `.gitignore`.

## Third-party code

- [ALTCHA](https://altcha.org/) (self-hosted bot check), in `interface/js/vendor/altcha/` with its licence.
- Inter and Cormorant Garamond fonts (SIL Open Font License), in `interface/fonts/`.

# LEXIS — Deployment Guide

How to run LEXIS on the ESSU server (or, as a fallback, a local machine at
the Legal Office). Everything here is free and self-hosted: PostgreSQL,
Uvicorn and Caddy. Documents go to the Legal Office's Google Drive (ESSU
Google Workspace) and email goes out through SMTP.

Versions: developed and tested with Python 3.13, PostgreSQL 16 and
Caddy 2.11. Final server versions are still to be confirmed with the Legal
Office and ESSU IT.

```
 Internet ──443/80──> Caddy (HTTPS, security headers)
                        ├── /api/*  ──> Uvicorn 127.0.0.1:8000 (FastAPI, 1 worker)
                        │                 ├── PostgreSQL 127.0.0.1 (Docker or native)
                        │                 ├── Google Drive API (documents)
                        │                 └── SMTP (email)
                        └── everything else ──> interface/ (static files)
```

Only Caddy is reachable from outside. Uvicorn and PostgreSQL listen on
127.0.0.1 only.

---

## 1. What to get from ESSU IT first

| Item | Used for |
| --- | --- |
| A server: Linux (Ubuntu LTS recommended) or Windows, always on, with a public IP | running LEXIS |
| A subdomain, e.g. `lexis.essu.edu.ph`, with a DNS **A record** to that IP | the site address |
| Ports **80 and 443** open from the internet to the server; nothing else | Caddy (80 is needed to get and renew the HTTPS certificate) |
| Outgoing access to `www.googleapis.com`, `oauth2.googleapis.com`, the SMTP host on port 587, and `www.officialgazette.gov.ph` | Drive, email, holiday import |
| A Google Cloud **service account** + JSON key, added as **Content manager** on a **Shared Drive owned by ESSU** (not a personal Drive), and the ID of the folder LEXIS should use | document storage |
| SMTP details for `legaloffice@essu.edu.ph` (App Password or relay) and the account's daily sending limit | email after turnover |

Until the office mailbox is ready, the interim Gmail sender is used (see
`backend/.env.example`, "Outgoing email").

---

## 2. Get the code and the Python environment

The examples use `/opt/lexis` (Linux) and `C:\LEXIS` (Windows). Any folder works.

Linux:
```sh
sudo useradd --system --create-home --shell /usr/sbin/nologin lexis
sudo git clone <repository URL> /opt/lexis
sudo chown -R lexis:lexis /opt/lexis
cd /opt/lexis
sudo -u lexis python3 -m venv .venv
sudo -u lexis .venv/bin/pip install -r backend/requirements.txt
sudo apt-get install -y tesseract-ocr          # holiday import (OCR)
```

Windows (PowerShell as Administrator):
```powershell
git clone <repository URL> C:\LEXIS
cd C:\LEXIS
py -3.13 -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
winget install --id UB-Mannheim.TesseractOCR   # holiday import (OCR)
```

---

## 3. The `.env` file

LEXIS reads its settings from `backend/.env`. Start from the template:
copy `backend/.env.example` to `backend/.env` and fill it in. For
production these values **must** be set:

```ini
# Never "development" on the server: development mode puts sign-in codes in
# API responses and prints them to the log.
ENVIRONMENT=production

# 32+ random characters. LEXIS refuses to start outside development without it.
# Make one with:  python -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=

# Cookies only over HTTPS.
SESSION_COOKIE_SECURE=true

PUBLIC_SITE_URL=https://lexis.essu.edu.ph
CORS_ALLOWED_ORIGINS=["https://lexis.essu.edu.ph"]

# Database. Use letters and digits only in the password (it sits inside a URL).
# Make one with:  python -c "import secrets; print(secrets.token_hex(24))"
DB_PASSWORD=
# Docker (section 4A): port 5433.  Native PostgreSQL (4B): port 5432.
DATABASE_URL=postgresql+psycopg://leo_user:<DB_PASSWORD>@127.0.0.1:5433/leo_db

# Documents in the Legal Office Shared Drive.
STORAGE_BACKEND=gdrive
GDRIVE_CREDENTIALS_FILE=/etc/lexis/drive-key.json      # Windows: C:\LEXIS-secrets\drive-key.json
GDRIVE_FOLDER_ID=

# Email: SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
# MAIL_FROM_ADDRESS, MAIL_FROM_NAME, MAIL_DAILY_LIMIT, MAIL_DAILY_CODE_LIMIT
# (both setups are explained in .env.example).

# Windows only, if Tesseract is not in C:\Program Files\Tesseract-OCR:
# TESSERACT_CMD=
```

### Keeping `.env` and the Drive key safe

- `.env` and the Drive key file are **never** committed, emailed or pasted
  into chat. Both are already ignored by Git; keep the Drive key **outside**
  the repository folder.
- Only the account that runs LEXIS (and administrators) may read them.
  - Linux:
    ```sh
    sudo chown lexis:lexis /opt/lexis/backend/.env && sudo chmod 600 /opt/lexis/backend/.env
    sudo mkdir -p /etc/lexis && sudo chown lexis:lexis /etc/lexis && sudo chmod 700 /etc/lexis
    # copy the key to /etc/lexis/drive-key.json, then:
    sudo chown lexis:lexis /etc/lexis/drive-key.json && sudo chmod 600 /etc/lexis/drive-key.json
    ```
  - Windows (replace `lexis` with the account that runs the task in section 6B):
    ```powershell
    icacls C:\LEXIS\backend\.env /inheritance:r /grant:r "Administrators:F" "lexis:R"
    icacls C:\LEXIS-secrets /inheritance:r /grant:r "Administrators:(OI)(CI)F" "lexis:(OI)(CI)R"
    ```
- Keep one copy of `.env` and the Drive key in a safe place **separate from
  the database backups**, so a lost backup does not also leak the keys.
- If a secret leaks, replace it: a new `SECRET_KEY` signs everyone out; also
  change `DB_PASSWORD` (in PostgreSQL too), the SMTP App Password, and ask
  ESSU IT to delete the Drive key and issue a new one.

---

## 4. Database

PostgreSQL listens on 127.0.0.1 only. LEXIS creates its tables and default
data on first start, so a new database needs no SQL files. (The
`batch-*-migration.sql` files were only for updating the development
database.)

### 4A. Linux: PostgreSQL in Docker

Install Docker Engine (free) from the official Docker repository, then
start **only the `db` service** (the `api` service in the same file is for
development):

```sh
cd /opt/lexis/backend
sudo docker compose up -d db
```

`backend/docker-compose.yml` already has `restart: unless-stopped`, so the
database comes back by itself after a reboot or crash, and binds it to
`127.0.0.1:5433`. Docker Compose reads `DB_PASSWORD` from `backend/.env`.
The password is set when the database volume is first created; changing it
later needs `ALTER USER leo_user PASSWORD '...'` as well.

### 4B. Windows: native PostgreSQL (recommended over Docker here)

On Windows, Docker needs Docker Desktop, which only starts after someone
logs in (so the database would stay down after an unattended reboot), and
its free licence does not cover large organizations. Instead, install
PostgreSQL 16 with the official Windows installer, which runs it as a
Windows service that starts at boot. Then, in `psql` as `postgres`:

```sql
CREATE USER leo_user WITH PASSWORD '<DB_PASSWORD>';
CREATE DATABASE leo_db OWNER leo_user;
```

In `postgresql.conf` keep `listen_addresses = 'localhost'`, and use port
5432 in `DATABASE_URL`.

---

## 5. First start and the first account

From `backend/` (Linux: as the `lexis` user):

```sh
../.venv/bin/python -c "import app.main"     # Windows: ..\.venv\Scripts\python -c "import app.main"
```

No error means the settings, database and Drive key load. Then create
the Head Attorney account (the only account made outside the app; the Head
Attorney creates everyone else in User Management). Use the real
`@essu.edu.ph` address:

```sh
BOOTSTRAP_HEAD_ATTORNEY_NAME="..." BOOTSTRAP_HEAD_ATTORNEY_EMAIL="...@essu.edu.ph" \
BOOTSTRAP_HEAD_ATTORNEY_PASSWORD="..." ../.venv/bin/python -m app.bootstrap_head_attorney
```
Windows:
```powershell
$env:BOOTSTRAP_HEAD_ATTORNEY_NAME="..."; $env:BOOTSTRAP_HEAD_ATTORNEY_EMAIL="...@essu.edu.ph"
$env:BOOTSTRAP_HEAD_ATTORNEY_PASSWORD="..."; ..\.venv\Scripts\python -m app.bootstrap_head_attorney
Remove-Item Env:BOOTSTRAP_HEAD_ATTORNEY_PASSWORD
```

---

## 6. Uvicorn as a service

Rules for both systems:

- Start it **in `backend/`** (LEXIS reads `.env` from the current folder).
- `--host 127.0.0.1`: never reachable directly. LEXIS trusts the
  `X-Forwarded-For` header from Caddy for rate limiting, which is only safe
  when nothing can bypass Caddy.
- `--workers 1`: rate limits, email daily counts and some caches are kept
  in memory, so several workers would each count separately.
- No `--reload` (that is for development).

### 6A. Linux: systemd

`/etc/systemd/system/lexis.service`:
```ini
[Unit]
Description=LEXIS API (Uvicorn)
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=lexis
WorkingDirectory=/opt/lexis/backend
ExecStart=/opt/lexis/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-server-header
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```sh
sudo systemctl daemon-reload
sudo systemctl enable --now lexis
sudo systemctl status lexis
journalctl -u lexis -f        # the log
```
If the database is still starting at boot, LEXIS fails and systemd starts
it again 5 seconds later.

### 6B. Windows: Task Scheduler (built in)

Create a local account (e.g. `lexis`, not an administrator) to run LEXIS,
then in PowerShell as Administrator:

```powershell
New-Item -ItemType Directory -Force C:\LEXIS\logs | Out-Null
$action = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory "C:\LEXIS\backend" `
  -Argument '/c C:\LEXIS\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-server-header >> C:\LEXIS\logs\lexis.log 2>&1'
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "LEXIS API" -Action $action -Trigger $trigger -Settings $settings `
  -User "lexis" -Password "<the lexis account's password>" -RunLevel Limited
Start-ScheduledTask -TaskName "LEXIS API"
```
Give the `lexis` account write access to `C:\LEXIS\logs` (and to
`C:\LEXIS\backend\storage` only if `STORAGE_BACKEND=local`). The log is
`C:\LEXIS\logs\lexis.log`.

What appears in the log: warnings and errors, e.g. Drive retries, quota
problems, unusual storage use and email failures.

---

## 7. Caddy with automatic HTTPS

Caddy gets and renews the HTTPS certificate (Let's Encrypt) by itself once
the DNS record points at the server and ports 80/443 are open.

Copy the repository's `Caddyfile` and change four things:

```caddyfile
{
	email legaloffice@essu.edu.ph      # certificate expiry notices
}

lexis.essu.edu.ph {                    # 1. the real address, no http:// (this turns HTTPS on)
	header {
		# ... keep every header from the development Caddyfile, and add:
		Strict-Transport-Security "max-age=31536000"   # 2. browsers always use HTTPS
	}
	header -Server

	handle_path /api/* {
		reverse_proxy 127.0.0.1:8000   # 3. same port as Uvicorn
	}

	handle {
		root * /opt/lexis/interface     # 4. full path (Windows: C:\LEXIS\interface)
		file_server
	}
}
```
Requests to `http://` are redirected to `https://` automatically.

- Linux: install Caddy from its official apt repository. It runs as the
  `caddy` service and reads `/etc/caddy/Caddyfile`. After editing:
  `sudo systemctl reload caddy`.
- Windows: download `caddy.exe` from caddyserver.com to `C:\caddy`, put the
  file at `C:\caddy\Caddyfile`, then register it as a service:
  ```powershell
  sc.exe create caddy start= auto binPath= "C:\caddy\caddy.exe run --config C:\caddy\Caddyfile"
  sc.exe start caddy
  ```

Check: `https://lexis.essu.edu.ph/home.html` loads with a padlock, and
`https://lexis.essu.edu.ph/api/health` answers `{"status":"ok"}`.

---

## 8. Nightly backups

What to back up:

| What | How |
| --- | --- |
| The database | nightly `pg_dump` (below) |
| Documents | in the Shared Drive (Google keeps them; a deleted file stays in the Shared Drive trash for 30 days). If `STORAGE_BACKEND=local`, also copy `backend/storage/` nightly |
| `.env` and the Drive key | once, and after each change, kept apart (section 3) |

A backup on the same machine does not survive a disk failure, so copy the
dump files somewhere else every day: a location ESSU IT provides, or a
restricted folder in the Legal Office Shared Drive. Dumps hold clients'
personal data (RA 10173): only the Legal Office and IT may read them.

### Linux (cron)

`/opt/lexis/backup.sh`:
```sh
#!/bin/sh
set -eu
DEST=/var/backups/lexis
mkdir -p "$DEST"
cd /opt/lexis/backend
docker compose exec -T db pg_dump -U leo_user -d leo_db -Fc > "$DEST/lexis-$(date +%F).dump"
find "$DEST" -name 'lexis-*.dump' -mtime +30 -delete      # keep 30 days
```
```sh
sudo chmod 700 /opt/lexis/backup.sh
sudo crontab -e        # add:  30 1 * * * /opt/lexis/backup.sh
```

### Windows (Task Scheduler)

Save the database password for the backup account in
`%APPDATA%\postgresql\pgpass.conf` (one line:
`127.0.0.1:5432:leo_db:leo_user:<DB_PASSWORD>`), then `C:\LEXIS\backup.ps1`:
```powershell
$dest = "D:\LEXIS-backups"
New-Item -ItemType Directory -Force $dest | Out-Null
# -f writes the file directly (PowerShell's ">" would damage a binary dump)
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -h 127.0.0.1 -p 5432 -U leo_user -d leo_db -Fc `
  -f "$dest\lexis-$(Get-Date -Format yyyy-MM-dd).dump"
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
Get-ChildItem $dest -Filter "lexis-*.dump" | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item
```
Schedule it daily at 1:30 AM with the same kind of task as section 6B
(`-Execute powershell.exe -Argument '-NoProfile -File C:\LEXIS\backup.ps1'`,
`New-ScheduledTaskTrigger -Daily -At 1:30am`).

### Test a restore (once a month)

A backup that was never restored is not known to work. Into a throwaway
database (Linux/Docker shown; on Windows use `createdb.exe`, `pg_restore.exe`,
`psql.exe` and `dropdb.exe` from the same `bin` folder):

```sh
cd /opt/lexis/backend
docker compose exec -T db createdb -U leo_user lexis_restore_test
docker compose exec -T db pg_restore -U leo_user -d lexis_restore_test < /var/backups/lexis/lexis-YYYY-MM-DD.dump
docker compose exec -T db psql -U leo_user -d lexis_restore_test -c "SELECT count(*) FROM legalservicerequest;"
docker compose exec -T db dropdb -U leo_user lexis_restore_test
```
The count should match the number of requests in LEXIS that day.

---

## 9. Updating LEXIS

1. Run the backup (section 8) first.
2. `git pull` in the LEXIS folder.
3. `pip install -r backend/requirements.txt` with the `.venv` pip.
4. If the update came with a `batch-*-migration.sql`, run it on the
   database (after the backup).
5. Restart: `sudo systemctl restart lexis` (Linux) or stop and start the
   "LEXIS API" task (Windows).
6. Check `/api/health` and sign in once.

---

## 10. Check after deploying

- [ ] `ENVIRONMENT=production`, a strong `SECRET_KEY`, `SESSION_COOKIE_SECURE=true`
- [ ] Ports 8000 and 5432/5433 are **not** reachable from another computer; 80/443 are
- [ ] The site opens on `https://` with a valid certificate; `http://` redirects
- [ ] Head Attorney can sign in; sign-in codes arrive by email (not shown on screen)
- [ ] Upload a document, download it, and find its file in the Shared Drive folder
      (named with a random key, not the original name)
- [ ] Reboot the server: database, LEXIS and Caddy all come back by themselves
- [ ] The first nightly backup file exists and was copied off the server

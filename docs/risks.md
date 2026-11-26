# LEXIS — Deployment Risks

Risks that come with running LEXIS on ESSU's own infrastructure: the ESSU
server (or a local machine), the Legal Office's Google Drive (ESSU Google
Workspace) for documents, and SMTP for email. Each risk lists what LEXIS
already does about it, what is still to be done and by whom, and what to do
when it happens. See `docs/deployment.md` for the setup itself.

Ratings: **likelihood** and **impact** are Low / Medium / High for one
small office (a few staff, a few thousand documents a year).

| # | Risk | Likelihood | Impact |
| --- | --- | --- | --- |
| 1 | Google Drive API outage or quota limit | Low | Medium |
| 2 | Workspace account or project changes cut LEXIS off from the files | Medium | High |
| 3 | Confidentiality of documents in Drive (RA 10173) | Medium | High |
| 4 | Interim Gmail sender: limits, deliverability, ownership | Medium | Medium |
| 5 | Single server: hardware failure, data loss, no fallback | Medium | High |

---

## 1. Google Drive API outage or quota limit

**What could happen.** Google's Drive service is slow or down, the
Shared Drive or ESSU's storage is full, or LEXIS goes over an API limit.
Uploads and downloads fail. Everything else (requests, statuses, messages,
reports) keeps working because it is in the database.

Google's published defaults are far above LEXIS's needs (thousands of API
calls a minute per project, 750 GB of uploads a day per account,
400,000 items per Shared Drive). The more realistic limits are ESSU's pooled
Workspace storage and someone filling the Shared Drive by hand.

**Already in LEXIS** (`backend/app/core/storage.py`)
- Every Drive call has a timeout (10 s to connect, 60 s to transfer).
- At most 4 tries, waiting about 1, 2 and 4 s; Google's "try again after"
  answer is respected but capped at 20 s. Never an endless loop.
- Quota and permission errors are not retried. The user sees a plain
  message ("The Legal Office's document storage has reached its limit…" or
  "…is not responding right now…") and the API answers 503 instead of
  crashing.
- Files are limited to 20 MB each (50 MB per submission).
- If a multi-file upload fails partway, the files already saved are removed
  so no orphans are left.
- Every retry, every quota or permission error, and unusual volume (more
  than 300 saves, 1,500 reads or 100 deletes, or 2 GB moved, in an hour) is
  written to the LEXIS log as a warning.

**Still to do**
- ESSU IT: confirm how much pooled storage the Legal Office may use and
  who is told when it runs low.
- Legal Office: whoever looks after LEXIS checks the log weekly for
  "Drive" and "Unusual storage use" lines.

**If it happens.** Check Google's status page (Workspace Status
Dashboard). If Google is fine, read the LEXIS log: a quota message means
storage is full (ask ESSU IT); a setup message means the key or folder
access changed (see risk 2). Staff can keep working on requests and add the
documents once storage is back.

---

## 2. Workspace account or project changes cut LEXIS off from the files

**What could happen.** The documents live in a Shared Drive and LEXIS
reaches them with a service account. Access breaks if:
- the files are kept in one person's My Drive and that person leaves ESSU
  (their account and files are deleted with them);
- the Google Cloud project that owns the service account belongs to a
  person, or is deleted or shut down;
- the service account is removed from the Shared Drive, or its key is
  deleted, expires or is blocked by an ESSU policy;
- the Shared Drive or folder is moved, renamed into another drive, or
  deleted.

**Already in LEXIS**
- Works only with a **Shared Drive** folder (`GDRIVE_FOLDER_ID`); Shared
  Drives belong to ESSU, not to a person, so files stay when staff leave.
- Stores only the bytes in Drive; the record of every document (which
  request, version, who uploaded it, who may see it) is in the LEXIS
  database, so a folder can be moved or restored without losing track.
- Refuses to start if `STORAGE_BACKEND=gdrive` is set without a key file
  and folder ID. A missing, wrong or blocked key shows the setup message to
  users and the cause in the log.
- Deleting in LEXIS moves the file to the Shared Drive trash (30 days), so a
  mistake can be undone.

**Still to do (ESSU IT)**
- Create the Google Cloud project **under the ESSU organization**, not a
  personal account, with at least two ESSU administrators as owners.
- Create the Shared Drive owned by ESSU; members: the LEXIS service account
  (Content manager) and one or two named administrators (Manager). Record
  who they are.
- Confirm ESSU's policies allow a service-account key (some Workspace
  organizations block key creation by default) and agree how often the key
  is replaced.
- Tell the Legal Office before any change to the project, the Shared Drive
  or Workspace storage policies.

**If it happens.** A Shared Drive manager re-adds the service account or
issues a new key; the new key path goes in `GDRIVE_CREDENTIALS_FILE` and
LEXIS is restarted. A deleted Shared Drive or file can be restored by a
Workspace administrator for a limited time after deletion, so act quickly.

---

## 3. Confidentiality of documents in Drive (RA 10173)

**What could happen.** Request documents hold personal and sometimes
sensitive information (legal matters, complaints, investigations). In
Drive, anyone who is a **member** of the Shared Drive can open every file
directly, without LEXIS's role checks or History log. A file could also be
shared by link or to someone outside ESSU from inside Drive.

**Already in LEXIS**
- Files are never shared by Drive link. Every download goes through LEXIS,
  which checks who is asking (staff role and assignment, or the client's
  own signed-in request) and only then fetches the file.
- Files in Drive are named with a random key, not the client's file name,
  request ID or name, so the folder listing reveals nothing by itself.
- The service account's key stays on the server, readable only by the
  account that runs LEXIS (deployment guide, section 3).

**Still to do**
- ESSU IT, Shared Drive settings: allow **no** sharing outside ESSU; allow
  **no** access for people who are not members; keep membership to the
  service account and the named administrators only (no Legal Office staff
  need it, they use LEXIS).
- Legal Office / ESSU DPO: add Google Drive (ESSU Google Workspace) as
  where documents are stored to ESSU's records of processing, and update
  the LEXIS **Privacy Notice** (`interface/privacy.html`, "Who sees it"),
  which today names only the email provider.
- ESSU IT: confirm the ESSU Google Workspace agreement covers this use
  (Google as ESSU's data processor) and that Workspace administrator
  access to Drive content is limited and logged.

**If it happens.** A file shared or opened by someone who should not have
it is a possible personal data breach: tell the ESSU Data Protection
Officer at once. Under NPC Circular 16-03 a breach that must be reported
goes to the National Privacy Commission, and to affected clients, within
72 hours of knowing about it. Drive's activity log (Workspace audit log)
shows who opened or shared the file.

---

## 4. Interim Gmail sender: limits, deliverability, ownership

**What could happen.** Until the office mailbox is ready, email goes out
through a free Gmail account, a temporary exception to the "no
subscriptions of LEXIS's own" rule.
- Free Gmail allows about 500 emails a day; going over can suspend sending
  for a day or lock the account.
- Mail from a `@gmail.com` address signed "ESSU Legal Office" may land in
  spam or look like phishing to clients.
- Google may ask the account to re-verify, and changing its password
  cancels the App Password, so email stops until someone fixes it.
- The account belongs to whoever created it, not to ESSU.

**Already in LEXIS** (`backend/app/core/mailer.py`)
- A daily cap (`MAIL_DAILY_LIMIT` 300) well under Gmail's limit, of which
  sign-in codes may use at most 200, so status updates always get through;
  reaching either cap is written to the Security Log.
- Timeouts, at most 2 tries per email, and a wrong password is never
  retried. Email failures never stop a request from being saved.
- Emails never contain the client's description or files, only the
  Request ID and the update; the client reads messages inside LEXIS.
- Changing to the office mailbox is a `.env` change only (see
  `backend/.env.example`, option B).

**Still to do**
- Use a dedicated Gmail account made for LEXIS only, with 2-Step
  Verification, its recovery details held by the Legal Office.
- ESSU IT: SMTP access for `legaloffice@essu.edu.ph` and its daily limit
  (pending). At turnover, switch `.env` to option B, send a test sign-in
  code, then remove the App Password and close or hand over the Gmail
  account.

**If it happens.** "SMTP login was rejected" in the log means a new App
Password is needed. "Daily email limit reached" in the Security Log means
the cap was hit: clients can still see every update in My Requests, and
sending starts again the next day. If clients report mail in spam, tell
them to mark it "Not spam", and move the turnover forward.

---

## 5. Single server: hardware failure, data loss, no fallback

**What could happen.** LEXIS (database, Uvicorn, Caddy) runs on one
machine. A disk failure, theft, ransomware, a bad update or a long power
cut takes LEXIS down, and without a copy elsewhere the database (requests,
history, messages, accounts) is lost. On the local-machine fallback the
risk is higher (no UPS, someone may switch it off).

**Already in LEXIS**
- Documents are in the Shared Drive, not on the server (with
  `STORAGE_BACKEND=gdrive`), so a dead server does not lose them.
- The database restarts by itself after a crash or reboot
  (`restart: unless-stopped`, or the Windows service), and so do Uvicorn
  and Caddy (deployment guide, sections 4, 6 and 7).
- The deployment guide (section 8) has the nightly `pg_dump`, 30 days kept,
  a daily copy off the server, and a monthly test restore.

**Still to do**
- ESSU IT: a place off the server for the nightly dumps, readable only by
  the Legal Office and IT; a UPS for the server.
- Legal Office: one person responsible for checking that the nightly
  backup exists and for the monthly restore test.
- Keep `.env` and the Drive key in a safe place apart from the dumps
  (needed to rebuild the server).

**If it happens.** Set up a new machine with the deployment guide,
restore the newest dump with `pg_restore`, put back `.env` and the Drive
key, and point the subdomain's DNS at the new machine. Anything entered
after the last nightly dump is lost and must be re-encoded from the
emails and paper forms. Downtime: about a day once a machine is available.

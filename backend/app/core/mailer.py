"""
Outgoing email over SMTP.

Development / thesis defense: a dedicated Gmail account (with an App
Password). At turnover: the Legal Office mailbox — only the SMTP_* and
MAIL_FROM_* values in .env change, never this code.

Safety rails:
- every connection has a timeout, so a slow mail server can't hang the API
- at most MAX_ATTEMPTS tries per email, with a pause between them
  (no infinite retry loops); a wrong password is never retried
- a daily sending cap (MAIL_DAILY_LIMIT) so a bot hammering "send code"
  can't burn through Gmail's quota and get the account suspended; hitting
  it is recorded as a SecurityEvent
- inside that cap, sign-in codes may use at most MAIL_DAILY_CODE_LIMIT, so
  the rest is always kept for status updates to clients
- nothing secret (passwords, codes) is ever written to the logs
"""
import logging
import re
import smtplib
import ssl
import threading
import time
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr

from sqlmodel import Session

from app.core.config import settings

logger = logging.getLogger("lois.mailer")

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 2

# A plain address only: no display name ("x1<victim@example.com>"), no
# lists (commas, semicolons), quotes, brackets or line breaks. Each of those
# looks like a new address to the hourly limits but delivers to someone else,
# so it could use up the day's email budget.
_PLAIN_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def is_plain_address(address: str) -> bool:
    return bool(_PLAIN_ADDRESS.fullmatch(address or ""))
SMTP_TIMEOUT_SECONDS = 10

# In-memory daily counter. Resets at midnight and on server restart —
# fine for a single server; move to the database if LEXIS ever runs on
# several servers at once.
_lock = threading.Lock()
_count_date = date.today()
_sent_today = 0
_codes_today = 0
_limit_event_logged = False
_code_limit_event_logged = False


def email_is_configured() -> bool:
    return all([
        settings.smtp_host,
        settings.smtp_username,
        settings.smtp_password,
        settings.mail_from_address,
    ])


def _record_limit_reached(which: str) -> None:
    # Imported here to avoid a circular import at startup.
    from app.core.database import engine
    from app.core.security_monitoring import log_security_event
    try:
        with Session(engine) as session:
            if which == "code":
                log_security_event(
                    session, "code_email_limit_reached",
                    f"Stopped sending sign-in codes after {settings.mail_daily_code_limit} today; "
                    "status updates still go out.", "server",
                )
            else:
                log_security_event(
                    session, "email_daily_limit_reached",
                    f"Stopped sending after {settings.mail_daily_limit} emails today.", "server",
                )
    except Exception:
        logger.exception("Could not record the email limit SecurityEvent.")


def _reserve_daily_slot(kind: str = "general") -> bool:
    """kind "code" = a sign-in code (its own, smaller budget); anything else
    = updates and staff mail."""
    global _count_date, _sent_today, _codes_today, _limit_event_logged, _code_limit_event_logged
    with _lock:
        today = date.today()
        if today != _count_date:
            _count_date, _sent_today, _codes_today = today, 0, 0
            _limit_event_logged = _code_limit_event_logged = False
        if kind == "code" and _codes_today >= settings.mail_daily_code_limit:
            report = None if _code_limit_event_logged else "code"
            _code_limit_event_logged = True
        elif _sent_today >= settings.mail_daily_limit:
            report = None if _limit_event_logged else "all"
            _limit_event_logged = True
        else:
            _sent_today += 1
            if kind == "code":
                _codes_today += 1
            return True
    if report:
        _record_limit_reached(report)   # logged once a day, outside the lock
    return False


def code_email_available() -> bool:
    """Whether a sign-in code email can still go out today, checked before
    the door promises one (MAIL-01). Reaching the limit is logged once a day,
    as when a send is refused."""
    global _count_date, _sent_today, _codes_today, _limit_event_logged, _code_limit_event_logged
    with _lock:
        today = date.today()
        if today != _count_date:
            _count_date, _sent_today, _codes_today = today, 0, 0
            _limit_event_logged = _code_limit_event_logged = False
        if _codes_today >= settings.mail_daily_code_limit:
            report = None if _code_limit_event_logged else "code"
            _code_limit_event_logged = True
        elif _sent_today >= settings.mail_daily_limit:
            report = None if _limit_event_logged else "all"
            _limit_event_logged = True
        else:
            return True
    if report:
        _record_limit_reached(report)
    return False


def _release_daily_slot(kind: str) -> None:
    """Gives back a slot when the email was never handed to the mail server,
    so a mail outage doesn't use up the day's budget (MAIL-03)."""
    global _sent_today, _codes_today
    with _lock:
        if date.today() != _count_date:
            return  # the count has started again since; nothing to give back
        _sent_today = max(_sent_today - 1, 0)
        if kind == "code":
            _codes_today = max(_codes_today - 1, 0)


def _open_connection() -> smtplib.SMTP:
    context = ssl.create_default_context()
    if settings.smtp_port == 465:  # SSL from the start
        return smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                timeout=SMTP_TIMEOUT_SECONDS, context=context)
    smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
    smtp.starttls(context=context)  # port 587: upgrade to encrypted before logging in
    return smtp


def send_email(to_address: str, subject: str, body: str, kind: str = "general") -> bool:
    """Sends a plain-text email. Returns True if it was handed to the mail
    server, False if it was not (reason is logged). Never raises for mail
    problems, so a caller in a background task can't crash. kind="code" for
    sign-in codes (see MAIL_DAILY_CODE_LIMIT)."""
    if not email_is_configured():
        logger.error("Email is not configured (SMTP_* settings missing); not sending.")
        return False
    if not is_plain_address(to_address):
        # Checked before the daily budget, so it never uses a slot.
        logger.error("Not a plain email address; not sending.")
        return False
    if not _reserve_daily_slot(kind):
        logger.error("Daily email limit reached for %s emails; not sending.", kind)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.mail_from_name, settings.mail_from_address))
    msg["To"] = to_address
    msg.set_content(body)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with _open_connection() as smtp:
                smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(msg)
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP login was rejected. Check SMTP_USERNAME and SMTP_PASSWORD (App Password).")
            _release_daily_slot(kind)
            return False  # retrying a wrong password never helps
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("Email attempt %s/%s failed: %s", attempt, MAX_ATTEMPTS, type(exc).__name__)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)

    logger.error("Email could not be sent after %s attempts.", MAX_ATTEMPTS)
    _release_daily_slot(kind)
    return False
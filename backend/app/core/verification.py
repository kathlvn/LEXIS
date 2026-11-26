"""
Email-verification code issuance for staff accounts.

TODO — REAL EMAIL PROVIDER NEEDED:
No SMTP/email-provider is wired up anywhere in this backend yet (the
public site's client-facing OTP is faked the same way — see
submit-request.js). send_verification_email() just logs the code
server-side for now so the flow is testable end-to-end. Swap this for a
real provider (SMTP, SendGrid, SES, etc.) before this goes anywhere near
production — until then, nobody actually receives these emails.
"""
import logging
from urllib.parse import quote

from sqlmodel import Session

from app.core.config import settings
from app.core.mailer import email_is_configured, send_email
from app.core.working_days import to_ph_date

from app.core.security import (
    generate_verification_code,
    hash_verification_code,
    new_verification_code_expiry,
)
from app.models.models import StaffEmailVerificationCode, StaffUser
logger = logging.getLogger("lois.verification")

def issue_verification_code(session: Session, staff: StaffUser) -> str:
    """Creates a new hashed verification-code row for this staff member and
    'sends' it. Returns the raw code — callers may echo it back in a
    dev-only response field until real email sending exists."""
    code = generate_verification_code()
    record = StaffEmailVerificationCode(
        staff_id=staff.id,
        code_hash=hash_verification_code(code),
        expires_at=new_verification_code_expiry(),
    )
    session.add(record)
    session.commit()
    send_verification_email(staff.email, code)
    return code


def send_verification_email(to_email: str, code: str) -> None:
    """Staff account verification code."""
    if email_is_configured():
        send_email(
            to_email,
            "Verify your LEXIS staff account",
            "Your LEXIS staff account verification code is:\n\n"
            f"    {code}\n\n"
            f"It expires in {settings.verification_code_expire_minutes} minutes.\n"
            "If you didn't expect this email, you can ignore it.\n\n"
            "— ESSU Legal Office",
        )
    if settings.environment == "development":
        print(f"[DEV] Verification code for {to_email}: {code}")


def send_requester_access_code(channel: str, destination: str, code: str) -> None:
    """
    Sends a public requester their one-time access code. Runs as a
    background task, so the requester's page answers immediately and the
    reply time can't hint at whether their details matched.

        Email: sent through the SMTP settings in .env. Email only:
    there is no SMS sender, and the door refuses contact
    numbers before a code is ever made.
    In development the code is also printed, so testing still works if
    email is slow or not set up.
    """
    if channel == "email" and email_is_configured():
        send_email(
            destination,
            "Your ESSU Legal Office verification code",
            "Your verification code is:\n\n"
            f"    {code}\n\n"
            f"It expires in {settings.requester_code_expire_minutes} minutes and can only be used once.\n"
            "Never share this code with anyone. Legal Office staff will never ask for it.\n"
            "If you didn't request this code, you can ignore this email.\n\n"
            "— ESSU Legal Office",
            kind="code",   # counts against the sign-in code budget (MAIL_DAILY_CODE_LIMIT)
        )
    if settings.environment == "development":
        print(f"[DEV] Requester access code via {channel} to {destination}: {code}")
    elif not (channel == "email" and email_is_configured()):
        logger.error("No sender configured for requester codes via %s; code was not delivered.", channel)

def send_submission_confirmation(channel: str, destination: str, tracking_id: str) -> None:
    """Confirmation after a successful submission. Contains only the
    Request ID — never the request's details."""
    if channel == "email" and email_is_configured():
        send_email(
            destination,
            f"We received your request — {tracking_id}",
            "Thank you. The ESSU Legal Office has received your request.\n\n"
            f"Your Request ID is:  {tracking_id}\n\n"
            "Please keep this ID. To check your request's status, open this link and\n"
            "sign in to My Requests:\n\n"
            f"    {request_link(tracking_id)}\n\n"
            "Sign in with this same email address; we'll send you a one-time code.\n\n"
            "If you didn't submit this request, please contact the Legal Office.\n\n"
            "— ESSU Legal Office",
        )
    if settings.environment == "development":
        print(f"[DEV] Submission confirmation via {channel} to {destination}: {tracking_id}")


def request_link(tracking_id: str) -> str:
    """Link to the request's page (request.html). It does NOT sign anyone
    in: without a My Requests sign-in (email + one-time code), the page
    sends the reader to the sign-in, and only the request's own client can
    open it. So a forwarded email gives no access to the request."""
    base = settings.public_site_url.rstrip("/")
    return f"{base}/request.html?id={quote(tracking_id)}"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def send_request_update(destination_email: str | None, tracking_id: str, updates: list[str],
                        is_closed: bool = False) -> None:
    """
    Requester notification. Runs as a
    background task after staff change something the requester should know
    about. Several changes saved together arrive as ONE email.

    What it may contain: the Request ID, the new status,
    the Legal Office's note to the requester, the name of a document the
    requester must revise or that was released to them, and a notice that staff
    wrote them a message (never its text) — the requester's
    own information, sent to the address they verified with a code. Never the
    attorney's return comments, internal notes, staff names, or the
    description the requester submitted. Requesters with only a contact number
    (walk-ins) are not notified: email only, no SMS.

    is_closed: the request is cancelled, rejected or withdrawn, so the client
    can't send messages on it and the email doesn't point them there.
    """
    if not updates:
        return
    if not destination_email:
        if settings.environment == "development":
            print(f"[DEV] No email on file for {tracking_id}; update not sent: {updates}")
        return

    lines = "\n\n".join(f"  - {u}" for u in updates)
    try:
        _send_request_update_email(destination_email, tracking_id, lines, is_closed)
    except Exception:
        # A notification must never break or undo the staff action that
        # triggered it; log and move on.
        logger.exception("Could not send the update email for %s.", tracking_id)
    if settings.environment == "development":
        print(f"[DEV] Request update email to {destination_email} for {tracking_id}: {updates}")


def _send_request_update_email(destination_email: str, tracking_id: str, lines: str, is_closed: bool) -> None:
    if is_closed:
        footer = ("This is an automated message. This request is closed, so it no longer accepts messages.\n"
                  "If you need to reach the Legal Office about it, please contact the office during office hours.\n\n")
    else:
        footer = ("This is an automated message. To reach the Legal Office about your request,\n"
                  "use the Messages section of your request in My Requests.\n\n")
    if email_is_configured():
        send_email(
            destination_email,
            f"Update on your request \u2014 {tracking_id}",
            "There is an update on your request with the ESSU Legal Office.\n\n"
            f"Request ID:  {tracking_id}\n\n"
            f"{lines}\n\n"
            "To see the full details, open this link and sign in to My Requests:\n\n"
            f"    {request_link(tracking_id)}\n\n"
            "Sign in with this email address and a one-time code.\n"
            "The Legal Office will never ask you for that code.\n\n"
            f"{footer}"
            "\u2014 ESSU Legal Office",
        )


def note_update_line(note: str) -> str:
    return "The Legal Office is waiting for a response or document from you:\n" \
           f"      \"{_clip(note, 1000)}\""


def reminder_update_lines(note: str | None) -> list[str]:
    lines = ["Reminder: the Legal Office is still waiting for a response or document from you."]
    if note:
        lines.append(f"What is needed:\n      \"{_clip(note, 1000)}\"")
    return lines


def closure_update_line(reason: str) -> str:
    return f"Reason given by the Legal Office:\n      \"{_clip(reason, 1000)}\""


def revision_update_line(filename: str, what_to_correct: str) -> str:
    return f"Your document \"{_clip(filename, 255)}\" needs to be revised. Please upload a corrected copy.\n" \
           f"    What to correct:\n      \"{_clip(what_to_correct, 1000)}\""


def released_update_line(filename: str) -> str:
    return f"The Legal Office has released a document for you: \"{_clip(filename, 255)}\"."


def message_update_line(is_closed: bool = False) -> str:
    """A notice that staff wrote a message, never the message itself:
    the client reads it on their request page after signing in,
    so the text never passes through the email provider. Never a staff name."""
    # A closed request no longer accepts messages from the client.
    return "The Legal Office sent you a message. Open your request to read it" + ("." if is_closed else " and reply.")




def send_client_verification_update(destination_email: str | None, outcome: str,
                                    reason: str | None, valid_until=None) -> None:
    """Tells a client the result of the Legal Office's check of their
    details: "verified", "declined" or "revoked". A reason is
    included only when staff chose to share it. Runs as a background task;
    never raises. Clients without an email (walk-ins) aren't notified."""
    if not destination_email:
        return
    if outcome == "verified":
        # Stored in UTC; the client reads the Philippine date (MAIL-02).
        until = to_ph_date(valid_until).strftime("%B %d, %Y") if valid_until else ""
        subject = "Your details have been verified \u2014 ESSU Legal Office"
        lines = ["The ESSU Legal Office has verified your details.",
                 "You can now submit requests in My Requests"
                 + (f". Your verification is valid until {until}." if until else ".")]
    elif outcome == "declined":
        subject = "We could not verify your details \u2014 ESSU Legal Office"
        lines = ["The ESSU Legal Office could not verify your details, so you can't submit new",
                 "requests for now. Your existing requests are not affected.",
                 "Please contact the Legal Office during office hours."]
    else:
        subject = "Your verification was withdrawn \u2014 ESSU Legal Office"
        lines = ["The ESSU Legal Office has withdrawn the verification of your details, so you",
                 "can't submit new requests for now. Your existing requests are not affected.",
                 "Please contact the Legal Office during office hours."]
    if reason:
        lines.append(f"\nReason given by the Legal Office:\n      \"{_clip(reason, 500)}\"")
    portal = settings.public_site_url.rstrip("/") + "/portal.html"
    body = ("\n".join(lines) + f"\n\nMy Requests:  {portal}\n\n"
            "This is an automated message.\n\n\u2014 ESSU Legal Office")
    try:
        if email_is_configured():
            send_email(destination_email, subject, body)
    except Exception:
        logger.exception("Could not send the verification email (%s).", outcome)
    if settings.environment == "development":
        print(f"[DEV] Client verification email ({outcome}) to {destination_email}")
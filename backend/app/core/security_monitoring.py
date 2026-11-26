"""
Rate limiting (slowapi, in-memory) and abnormal-usage logging.

In-memory rate limiting means limits reset on restart and don't share
state across multiple server processes — fine for the current
single-instance deployment. Revisit with a Redis backend if this ever
scales out to multiple workers/instances.

Abnormal usage is logged to SecurityEvent rather than pushed anywhere —
no email/Slack/etc. is wired up yet, same gap already flagged for
verification emails. Query SecurityEvent directly for now; swap in
real alerting once there's somewhere to send it.
"""
import ipaddress
import logging
from datetime import datetime, timedelta

from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import Session, func, select

from app.core.config import settings
from app.models.models import SecurityEvent

FAILED_LOGIN_SPIKE_THRESHOLD = 5
FAILED_LOGIN_SPIKE_WINDOW_MINUTES = 10


def _trusted_proxies():
    nets = []
    for entry in settings.trusted_proxy_ips:
        try:
            nets.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            logging.getLogger("lois.security").warning("TRUSTED_PROXY_IPS: %r is not an address or range.", entry)
    return nets


_TRUSTED_PROXIES = _trusted_proxies()


def _is_trusted_proxy(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_PROXIES)


def get_client_ip(request) -> str:
    """
    The visitor's address, for rate limits and the Security Log.

    Caddy sits in front of this app, so the direct connection is Caddy's own
    address on every request; Caddy puts the visitor's address in
    X-Forwarded-For. That header is believed only when the connection comes
    from Caddy (TRUSTED_PROXY_IPS, default this machine). Anyone
    else reaching port 8000 directly could write any address there to dodge
    the rate limits, so for them the real connection address is used. Caddy
    adds its own entry last, so the last entry is the one taken.
    """
    direct = get_remote_address(request)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _is_trusted_proxy(direct):
        last = forwarded.split(",")[-1].strip()
        if last:
            return last[:45]
    return direct


# key_style="endpoint": each limit counts per endpoint, not per URL, so
# /staff/02 and /staff/002 (or 200 different Request IDs) share one count.
limiter = Limiter(key_func=get_client_ip, key_style="endpoint")


# Unusual activity: when an event type reaches its
# threshold within its time window, the Head Attorney gets a bell
# notification. It fires when the count REACHES the threshold, so a burst
# gives one alert, not one per event. The Security Log page shows the same
# thresholds. (event type: (count, minutes, what it means))
ALERT_RULES = {
    "failed_login_spike":        (1, 60, "Repeated failed staff logins from one address"),
    "rate_limit_exceeded":       (20, 60, "Many requests blocked by the rate limits"),
    "contact_code_locked":       (3, 60, "Several My Requests sign-in codes locked after wrong guesses"),
    "contact_code_hourly_cap":   (5, 60, "My Requests sign-in code limit reached many times"),
    "bot_check_failed":          (20, 60, "Many sign-in attempts failed the \"I'm not a robot\" check"),
    "code_email_limit_reached":  (1, 1440, "The daily limit for sign-in code emails was reached; codes are paused until tomorrow"),
    "email_daily_limit_reached": (1, 1440, "The daily email limit was reached; emails are paused until tomorrow"),
    "records_exported":          (3, 1440, "Records archive exported several times today"),
}


def log_security_event(session: Session, event_type: str, detail: str, source_ip: str) -> None:
    # Cut to the column sizes: details can include text an
    # attacker typed, which must never make the log write itself fail.
    event_type = event_type[:50]
    session.add(SecurityEvent(
        event_type=event_type, detail=detail[:500], source_ip=(source_ip or "")[:45],
    ))
    session.commit()
    try:
        _alert_if_unusual(session, event_type)
    except Exception:
        # An alert must never break the request that logged the event.
        session.rollback()


def _alert_if_unusual(session: Session, event_type: str) -> None:
    rule = ALERT_RULES.get(event_type)
    if not rule:
        return
    threshold, minutes, meaning = rule
    count = session.exec(
        select(func.count()).select_from(SecurityEvent).where(
            SecurityEvent.event_type == event_type,
            SecurityEvent.created_at >= datetime.utcnow() - timedelta(minutes=minutes),
        )
    ).one()
    if count != threshold:
        return
    from app.core.notifications import head_attorney_ids, notify   # avoids an import loop
    window = "today" if minutes >= 1440 else f"in the last {minutes} minutes"
    notify(session, head_attorney_ids(session), "security_alert",
           f"Security: {meaning} ({count} {window}). See the Security Log.")
    session.commit()


def check_failed_login_spike(session: Session, source_ip: str) -> None:
    """
    Call after logging a failed_login event. Escalates to a distinct
    failed_login_spike event once recent failures from this source cross
    the threshold — visible even if the failures are spread out just
    enough to dodge the per-route rate limit on /auth/login.
    """
    window_start = datetime.utcnow() - timedelta(minutes=FAILED_LOGIN_SPIKE_WINDOW_MINUTES)
    recent_failures = session.exec(
        select(SecurityEvent).where(
            SecurityEvent.event_type == "failed_login",
            SecurityEvent.source_ip == source_ip,
            SecurityEvent.created_at >= window_start,
        )
    ).all()
    if len(recent_failures) >= FAILED_LOGIN_SPIKE_THRESHOLD:
        log_security_event(
            session,
            "failed_login_spike",
            f"{len(recent_failures)} failed logins from this source in {FAILED_LOGIN_SPIKE_WINDOW_MINUTES} minutes.",
            source_ip,
        )

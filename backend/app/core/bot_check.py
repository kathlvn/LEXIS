"""
"I'm not a robot" check before a sign-in code is emailed.

ALTCHA, self-hosted: LEXIS makes a small puzzle, the visitor's browser solves
it (about a second, no clicking on pictures), and LEXIS checks the answer.
Nothing is sent to an outside company, and no cookies or tracking are used.
For one person this is invisible; for a script trying to make LEXIS send
thousands of emails, every single code costs real computing time.

Rules:
- each puzzle is signed with a key only the server knows, so it can't be
  made up or changed;
- each puzzle expires after `bot_check_expire_minutes`;
- each solved puzzle works ONCE (a replayed answer is refused).

The "used" list is kept in memory, like the mailer's daily counter: fine
for one server. A puzzle expires within minutes, so a restart can't
reopen more than a few minutes of old answers.
"""
import hashlib
import hmac
import threading
import time
from datetime import datetime, timedelta, timezone

from altcha import Payload, create_challenge, verify_solution

from app.core.config import settings

ALGORITHM = "PBKDF2/SHA-256"
MAX_PAYLOAD_LENGTH = 4000      # a real answer is far smaller; anything bigger is junk
MAX_REMEMBERED = 20000         # used answers kept at once; past this, new answers are refused

# Its own key, derived from SECRET_KEY, so no extra secret has to be managed.
_SECRET = hmac.new(settings.secret_key.encode("utf-8"), b"lois-bot-check", hashlib.sha256).hexdigest()

_lock = threading.Lock()
_used: dict[str, float] = {}   # puzzle signature -> time after which it no longer matters


def new_challenge() -> dict:
    """A fresh puzzle for the widget on the My Requests sign-in."""
    challenge = create_challenge(
        algorithm=ALGORITHM,
        cost=settings.bot_check_cost,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.bot_check_expire_minutes),
        hmac_secret=_SECRET,
    )
    return challenge.to_dict()


def bot_check_passed(payload_b64: str | None) -> bool:
    """True only for a correct, unexpired, signed answer that hasn't been
    used before. Never raises: anything unexpected counts as a failure."""
    if not settings.bot_check_enabled:
        return True
    if not payload_b64 or len(payload_b64) > MAX_PAYLOAD_LENGTH:
        return False
    try:
        if not verify_solution(payload_b64, _SECRET).verified:
            return False
        signature = Payload.from_base64(payload_b64).challenge.signature
    except Exception:
        return False
    if not signature:
        return False

    now = time.time()
    keep_until = now + settings.bot_check_expire_minutes * 60 + 60
    with _lock:
        if len(_used) >= MAX_REMEMBERED // 2:
            for key in [k for k, until in _used.items() if until < now]:
                del _used[key]
        if signature in _used or len(_used) >= MAX_REMEMBERED:
            return False   # replayed, or under a flood: refuse rather than forget
        _used[signature] = keep_until
    return True
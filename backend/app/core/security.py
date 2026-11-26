"""
Security utilities: password hashing (Argon2id), session tokens, and
email-verification codes for staff accounts.

Design notes:
- Passwords are hashed with Argon2id via argon2-cffi — OWASP's current
  recommendation over bcrypt/PBKDF2.
- Session tokens and verification codes are never stored in the DB as
  plaintext, only an HMAC-SHA256 digest keyed with settings.secret_key.
  A leaked DB backup alone can't be replayed as a live session or a valid
  code — you'd also need the server's secret_key.
- Sessions are opaque random tokens, not JWTs: the real state (staff_id,
  expiry, revocation) lives server-side in StaffSession, so revoking a
  session is an instant DB update instead of waiting out a token's life.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()  # Argon2id by default


# --------------------------- Passwords ---------------------------

MIN_PASSWORD_LENGTH = 12
# A password the Head Attorney chose (new account, temporary password)
# stops working after this many hours if it was never replaced.
TEMP_PASSWORD_HOURS = 72

# Words and patterns that make a 12-character password easy to guess
# Checked as parts of the password, ignoring capitals, spaces and
# simple swaps like 0 for o. A short list of our own, no package.
_WEAK_PARTS = (
    "password", "lexis", "essu", "qwerty", "asdfgh", "zxcvbn", "123456", "654321",
    "abcdef", "iloveyou", "letmein", "welcome", "admin", "changeme", "default",
)
_SWAPS = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def _plain(text: str) -> str:
    """Lower case, letters and digits only."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def password_problem(password: str, email: str | None = None, full_name: str | None = None) -> str | None:
    """What is wrong with a new staff password, or None if it is fine.
    Used for every password a person chooses or is given."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if len(set(password.lower())) < 5:
        return "Use more different characters. This password repeats the same few."
    plain = _plain(password)
    swapped = _plain(password.lower().translate(_SWAPS))
    if any(part in plain or part in swapped for part in _WEAK_PARTS):
        return ("Don't use common words or patterns such as “password”, “123456”, “qwerty”, "
                "“lexis” or “essu”.")
    own = []
    if email:
        local = email.split("@")[0].lower()
        own += [local] + [p for p in local.replace("_", ".").replace("-", ".").replace("+", ".").split(".")]
    if full_name:
        own += full_name.lower().split()
    if any(len(_plain(p)) >= 4 and (_plain(p) in plain or _plain(p) in swapped) for p in own):
        return "Don't use your name or email address in the password."
    return None


def hash_password(plain_password: str) -> str:
    return _ph.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Any other library-level issue (e.g. malformed hash) — fail closed.
        return False


# ------------------------ Shared token hashing ------------------------

def _hmac_digest(value: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# --------------------------- Sessions ---------------------------

def generate_session_token() -> str:
    """High-entropy random value — this is the raw token that goes in the
    cookie. Only its HMAC digest is ever stored in the DB."""
    return secrets.token_urlsafe(32)


def hash_session_token(raw_token: str) -> str:
    return _hmac_digest(raw_token)


def new_session_expiry() -> datetime:
    return datetime.utcnow() + timedelta(minutes=settings.session_expire_minutes)


# --------------------- Email verification codes ---------------------

def generate_verification_code() -> str:
    """6-digit numeric code — mirrors the pattern the public-facing OTP
    modal already uses, so the UX stays consistent app-wide."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_verification_code(code: str) -> str:
    return _hmac_digest(code)


def new_verification_code_expiry() -> datetime:
    return datetime.utcnow() + timedelta(minutes=settings.verification_code_expire_minutes)
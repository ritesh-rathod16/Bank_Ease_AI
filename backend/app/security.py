"""Password and opaque-token helpers for the account API."""
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, exceptions, profiles


_PASSWORDS = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
_DUMMY_PASSWORD_HASH = _PASSWORDS.hash("not-a-real-account-password-for-timing")


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters long")
    if len(password) > 128:
        raise ValueError("Password must be 128 characters or fewer")


def hash_password(password: str) -> str:
    validate_password(password)
    return _PASSWORDS.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    candidate = encoded or _DUMMY_PASSWORD_HASH
    try:
        valid = _PASSWORDS.verify(candidate, password)
    except (exceptions.VerificationError, exceptions.InvalidHashError, TypeError):
        valid = False
    return bool(valid and encoded)


def password_needs_rehash(encoded: str) -> bool:
    try:
        return _PASSWORDS.check_needs_rehash(encoded)
    except (exceptions.InvalidHashError, TypeError):
        return False


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)

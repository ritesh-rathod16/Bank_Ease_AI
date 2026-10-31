"""Validated runtime settings shared by the API and storage layer."""
from dataclasses import dataclass
import math
import os


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    environment: str
    storage_backend: str
    mongodb_uri: str | None
    mongodb_database: str
    mongo_connect_timeout_ms: int
    retention_hours: float
    retention_sweep_seconds: int


def load_settings(environ=None):
    values = os.environ if environ is None else environ
    environment = values.get("APP_ENV", "development").strip().lower()
    backend = values.get("BANKEASE_STORAGE_BACKEND", "local-json").strip().lower()
    uri = values.get("MONGODB_URI", "").strip() or None
    database = values.get("MONGODB_DATABASE", "bankease").strip()
    try:
        timeout_ms = int(values.get("MONGODB_CONNECT_TIMEOUT_MS", "5000"))
    except ValueError as exc:
        raise ConfigurationError("MONGODB_CONNECT_TIMEOUT_MS must be a positive integer") from exc
    try:
        retention_hours = float(values.get("BANKEASE_RETENTION_HOURS", "24"))
        retention_sweep_seconds = int(values.get("BANKEASE_RETENTION_SWEEP_SECONDS", "900"))
    except ValueError as exc:
        raise ConfigurationError("Retention settings must be numeric") from exc

    if environment not in {"development", "test", "production"}:
        raise ConfigurationError("APP_ENV must be development, test, or production")
    if backend not in {"local-json", "mongodb"}:
        raise ConfigurationError("BANKEASE_STORAGE_BACKEND must be local-json or mongodb")
    if backend == "mongodb" and not uri:
        raise ConfigurationError("MONGODB_URI is required when BANKEASE_STORAGE_BACKEND=mongodb")
    if not database or len(database) > 63 or any(c in database for c in " /\\.\"$*<>:|?\x00"):
        raise ConfigurationError("MONGODB_DATABASE must be a valid MongoDB database name")
    if timeout_ms <= 0:
        raise ConfigurationError("MONGODB_CONNECT_TIMEOUT_MS must be a positive integer")
    if not math.isfinite(retention_hours) or retention_hours < 0:
        raise ConfigurationError("BANKEASE_RETENTION_HOURS must be finite and non-negative; 0 disables expiry")
    if retention_sweep_seconds <= 0:
        raise ConfigurationError("BANKEASE_RETENTION_SWEEP_SECONDS must be a positive integer")
    if environment == "production" and backend != "mongodb":
        raise ConfigurationError("Production requires BANKEASE_STORAGE_BACKEND=mongodb")
    if environment == "production":
        raise ConfigurationError(
            "Production startup remains disabled until production email delivery, private file storage, distributed rate limits, deployment controls, and MongoDB operations are verified"
        )

    return Settings(environment, backend, uri, database, timeout_ms,
                    retention_hours, retention_sweep_seconds)

"""Environment for Clerk sign-in and the production listing database."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

AUTH_ENV = (
    "CLERK_SECRET_KEY",
    "CLERK_PUBLISHABLE_KEY",
    "CLERK_JWT_ISSUER",
)
DATABASE_ENV = "BABACLICK_PROD_DATABASE_URL"


def load_env_file() -> None:
    """Load noon_listing_updater/.env without overriding variables already set."""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def missing_auth_env() -> list[str]:
    load_env_file()
    return [name for name in AUTH_ENV if not os.getenv(name, "").strip()]


def production_database_url() -> str:
    load_env_file()
    return os.getenv(DATABASE_ENV, "").strip()


def clerk_secret_key() -> str:
    load_env_file()
    return os.getenv("CLERK_SECRET_KEY", "").strip()


def clerk_publishable_key() -> str:
    load_env_file()
    return os.getenv("CLERK_PUBLISHABLE_KEY", "").strip()


def clerk_issuer() -> str:
    load_env_file()
    return os.getenv("CLERK_JWT_ISSUER", "").strip().rstrip("/")


def clerk_authorized_parties() -> list[str]:
    """Same rule as Pforge: an empty list does not restrict azp."""
    load_env_file()
    raw = os.getenv("CLERK_AUTHORIZED_PARTIES", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def listen_port() -> int:
    """Public port. Render sets PORT; locally this is 8501."""
    load_env_file()
    raw = os.getenv("PORT", "8501").strip() or "8501"
    return int(raw)


def streamlit_port() -> int:
    """Streamlit stays on an internal port. The public port is the proxy."""
    public = listen_port()
    return 8510 if public != 8510 else 8511


def public_origin() -> str:
    """Browser origin of this service. Render sets RENDER_EXTERNAL_URL."""
    load_env_file()
    configured = os.getenv("APP_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{listen_port()}"


def sign_in_url() -> str:
    return public_origin() + "/sign-in"

"""Environment for Clerk sign-in and the production listing database."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"

AUTH_ENV = (
    "CLERK_OAUTH_CLIENT_ID",
    "CLERK_OAUTH_CLIENT_SECRET",
    "CLERK_JWT_ISSUER",
    "STREAMLIT_AUTH_COOKIE_SECRET",
)
DATABASE_ENV = "BABACLICK_PROD_DATABASE_URL"
DEFAULT_REDIRECT_URI = "http://localhost:8501/oauth2callback"


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


def redirect_uri() -> str:
    load_env_file()
    return os.getenv("CLERK_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip() or DEFAULT_REDIRECT_URI


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_auth_secrets() -> None:
    """Write the Streamlit auth file from the environment. No database URL is stored here."""
    missing = missing_auth_env()
    if missing:
        raise RuntimeError("Missing " + ", ".join(missing))
    issuer = os.environ["CLERK_JWT_ISSUER"].strip().rstrip("/")
    body = "\n".join(
        [
            "[auth]",
            f"redirect_uri = {_quote(redirect_uri())}",
            f"cookie_secret = {_quote(os.environ['STREAMLIT_AUTH_COOKIE_SECRET'].strip())}",
            f"client_id = {_quote(os.environ['CLERK_OAUTH_CLIENT_ID'].strip())}",
            f"client_secret = {_quote(os.environ['CLERK_OAUTH_CLIENT_SECRET'].strip())}",
            f'server_metadata_url = {_quote(issuer + "/.well-known/openid-configuration")}',
            "",
        ]
    )
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(body, encoding="utf-8")

"""Load .env, write Streamlit's Clerk auth file, and start the app."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from settings import (
    AUTH_ENV,
    DATABASE_ENV,
    DEFAULT_REDIRECT_URI,
    missing_auth_env,
    production_database_url,
    write_auth_secrets,
)

REQUIRED = (*AUTH_ENV, DATABASE_ENV)


def main() -> int:
    missing = missing_auth_env()
    if not production_database_url():
        missing.append(DATABASE_ENV)
    if missing:
        print("Set these environment variables in noon_listing_updater/.env:")
        for name in missing:
            print(f"  {name}")
        print(f"Clerk redirect URI: {DEFAULT_REDIRECT_URI}")
        return 1
    write_auth_secrets()
    app = str(Path(__file__).with_name("app.py"))
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        app,
        "--server.headless",
        "true",
        "--server.port",
        "8501",
        "--browser.gatherUsageStats",
        "false",
    ]
    return subprocess.call(command, cwd=str(Path(__file__).resolve().parent))


if __name__ == "__main__":
    raise SystemExit(main())

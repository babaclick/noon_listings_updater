"""Start Streamlit on an internal port and publish one port for Render."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from public_server import serve
from settings import (
    AUTH_ENV,
    DATABASE_ENV,
    listen_port,
    missing_auth_env,
    production_database_url,
    public_origin,
    sign_in_url,
    streamlit_port,
)

ROOT = Path(__file__).resolve().parent


def _streamlit_command() -> list[str]:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.headless",
        "true",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(streamlit_port()),
        "--server.enableCORS",
        "false",
        "--server.enableXsrfProtection",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    if os.getenv("RENDER", "").strip().lower() == "true":
        command.extend(["--server.fileWatcherType", "none"])
    return command


def _wait_for_streamlit(process: subprocess.Popen[bytes]) -> None:
    deadline = time.time() + 60
    url = f"http://127.0.0.1:{streamlit_port()}/_stcore/health"
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit("Streamlit exited before it accepted connections.")
        try:
            response = httpx.get(url, timeout=1)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise SystemExit("Streamlit did not become ready.")


def main() -> int:
    missing = missing_auth_env()
    if not production_database_url():
        missing.append(DATABASE_ENV)
    if missing:
        print("Set these environment variables on the Render service:")
        for name in missing:
            print(f"  {name}")
        print("CLERK_AUTHORIZED_PARTIES is the service origin, for example https://your-service.onrender.com")
        print("Copy CLERK_SECRET_KEY and CLERK_JWT_ISSUER from Pforge Backend.")
        print("Copy CLERK_PUBLISHABLE_KEY from Pforge Frontend VITE_CLERK_PUBLISHABLE_KEY.")
        return 1

    process = subprocess.Popen(_streamlit_command(), cwd=str(ROOT))

    def stop(*_args: object) -> None:
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    try:
        _wait_for_streamlit(process)
        print(f"Public origin: {public_origin()}")
        print(f"Sign-in: {sign_in_url()}")
        print(f"Listening on 0.0.0.0:{listen_port()}")
        serve()
        return 0
    finally:
        stop()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())

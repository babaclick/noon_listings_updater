"""Clerk session JWT verification, matching Pforge Backend pforge/clerk.py."""

from __future__ import annotations

import ssl
from typing import Any, Optional

import certifi
import httpx
import jwt
from jwt import PyJWKClient

from settings import clerk_authorized_parties, clerk_issuer, clerk_secret_key

_jwk_client: Optional[PyJWKClient] = None


class ClerkAuthError(Exception):
    def __init__(self, message: str, *, expired: bool = False) -> None:
        super().__init__(message)
        self.expired = expired


def _jwks_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        issuer = clerk_issuer()
        _jwk_client = PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_keys=True,
            ssl_context=ssl.create_default_context(cafile=certifi.where()),
        )
    return _jwk_client


def verify_clerk_token(token: str) -> dict[str, Any]:
    """Verify a Clerk session JWT with the instance JWKS, issuer, and azp list."""
    if not clerk_secret_key() or not clerk_issuer():
        raise ClerkAuthError("Clerk auth is not configured. Set CLERK_SECRET_KEY and CLERK_JWT_ISSUER.")
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise ClerkAuthError("Missing Clerk session token.")

    issuer = clerk_issuer()
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(raw)
        claims = jwt.decode(
            raw,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ClerkAuthError("Token expired.", expired=True) from exc
    except jwt.PyJWKClientConnectionError as exc:
        raise ClerkAuthError("Clerk JWKS unavailable.") from exc
    except jwt.InvalidTokenError as exc:
        raise ClerkAuthError("Invalid token.") from exc

    parties = clerk_authorized_parties()
    if parties:
        azp = claims.get("azp")
        if azp not in parties:
            raise ClerkAuthError(
                f"Unauthorized party (azp={azp!r}). "
                "Add this exact value to CLERK_AUTHORIZED_PARTIES."
            )
    return claims


def _backend_headers() -> dict[str, str]:
    secret = clerk_secret_key()
    if not secret:
        raise ClerkAuthError("Clerk auth is not configured. Set CLERK_SECRET_KEY and CLERK_JWT_ISSUER.")
    return {"Authorization": f"Bearer {secret}"}


def clerk_user_label(user_id: str) -> str:
    """Email or name from the Clerk Backend API. Falls back to the user id."""
    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers=_backend_headers(),
            timeout=15,
        )
    except httpx.HTTPError:
        return user_id
    if response.status_code != 200:
        return user_id
    body = response.json()
    emails = body.get("email_addresses") or []
    primary = body.get("primary_email_address_id")
    for email in emails:
        if email.get("id") == primary and email.get("email_address"):
            return str(email["email_address"])
    for email in emails:
        if email.get("email_address"):
            return str(email["email_address"])
    name = " ".join(
        part for part in (body.get("first_name") or "", body.get("last_name") or "") if part
    ).strip()
    return name or user_id


def session_is_active(session_id: str) -> bool:
    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/sessions/{session_id}",
            headers=_backend_headers(),
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ClerkAuthError("Clerk session check failed.") from exc
    if response.status_code == 404:
        return False
    if response.status_code != 200:
        raise ClerkAuthError(f"Clerk session check failed ({response.status_code}).")
    return response.json().get("status") == "active"


def revoke_session(session_id: str) -> None:
    if not session_id:
        return
    try:
        httpx.post(
            f"https://api.clerk.com/v1/sessions/{session_id}/revoke",
            headers=_backend_headers(),
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ClerkAuthError("Clerk sign-out failed.") from exc

"""Noon Partner calls for changing an existing listing. Login uses the page credentials."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import jwt

CONTENT_PATH = "/content/v1/product/upsert"
PRICING_PATH = "/pricing/v1/pricing/upsert"
STOCK_PATH = "/stock/v1/stock-update"
BARCODE_PATH = "/catplat/v1/barcode/map"
_LOGIN_PATH = "/identity/public/v1/api/login"


def _values(*items: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {"values": list(items)}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


class NoonClient:
    def __init__(
        self,
        *,
        private_key: str,
        key_id: str,
        project_code: str,
        base_url: str,
        timeout_s: float = 60.0,
    ) -> None:
        key = private_key.strip().replace("\\n", "\n")
        if not key or not key_id.strip() or not project_code.strip():
            raise ValueError("Noon key id, private key, and project code are required.")
        self._private_key = key
        self._key_id = key_id.strip()
        self.project_code = project_code.strip()
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout_s, follow_redirects=True)
        self._logged_in = False

    def close(self) -> None:
        self._http.close()

    def login(self) -> None:
        if self._logged_in:
            return
        token = jwt.encode(
            {"sub": self._key_id, "iat": int(time.time()), "jti": str(uuid.uuid4())},
            self._private_key,
            algorithm="RS256",
        )
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        response = self._http.post(
            f"{self.base_url}{_LOGIN_PATH}",
            json={"token": token, "default_project_code": self.project_code},
            headers=self._headers(),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Noon login failed HTTP {response.status_code}: {response.text[:300]}")
        self._logged_in = True

    def _headers(self) -> dict[str, str]:
        return {
            "X-Project": self.project_code,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.login()
        response = self._http.post(self.base_url + path, json=body, headers=self._headers())
        try:
            payload = response.json()
        except Exception:
            payload = (response.text or "")[:500]
        detail = payload if isinstance(payload, str) else str(payload)[:500]
        if response.status_code >= 400:
            return {"ok": False, "status_code": response.status_code, "detail": detail}
        return {"ok": True, "status_code": response.status_code, "detail": detail}


def content_body(partner_sku: str, changes: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    title = _text(changes.get("title"))
    if title:
        attributes["product_title"] = _values({"value": title[:200], "language": "LANGUAGE_EN"})
    brand = _text(changes.get("brand"))
    description = _text(changes.get("description"))
    if description:
        attributes["long_description"] = _values(
            {"value": description[:2500], "language": "LANGUAGE_EN"}
        )
    weight = changes.get("weight_g")
    if weight is not None:
        attributes["shipping_weight"] = _values({"value": weight})
        attributes["shipping_weight_unit"] = _values({"value": "gram"})
    body: dict[str, Any] = {"skus": [{"partner_sku": partner_sku}], "attributes": attributes}
    if brand:
        body["brand"] = brand
    return body


def pricing_body(partner_sku: str, price: float) -> dict[str, Any]:
    return {
        "items": [
            {
                "partner_sku": partner_sku,
                "country_code": "ae",
                "price": round(float(price), 2),
                "is_active": True,
            }
        ]
    }


def stock_body(partner_sku: str, qty: int) -> dict[str, Any]:
    return {"items": [{"partner_sku": partner_sku, "qty": int(qty)}]}


def barcode_body(partner_sku: str, gtin: str) -> dict[str, Any]:
    return {"items": [{"barcode": gtin, "partner_sku": partner_sku, "force_map": False}]}


def apply_changes(client: NoonClient, partner_sku: str, changes: dict[str, Any]) -> list[dict[str, Any]]:
    """Send only the Noon fields present in changes. Returns one result per call."""
    results: list[dict[str, Any]] = []
    content_keys = {"title", "weight_g", "brand", "description"}
    if content_keys & set(changes):
        results.append({"call": "content", **client.post(CONTENT_PATH, content_body(partner_sku, changes))})
    if "price" in changes:
        results.append({"call": "price", **client.post(PRICING_PATH, pricing_body(partner_sku, changes["price"]))})
    if "stock" in changes:
        results.append({"call": "stock", **client.post(STOCK_PATH, stock_body(partner_sku, changes["stock"]))})
    if "gtin" in changes:
        results.append({"call": "barcode", **client.post(BARCODE_PATH, barcode_body(partner_sku, changes["gtin"]))})
    return results

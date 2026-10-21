"""Match spreadsheet columns to Noon listing fields."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Fields this updater can send to an existing Noon listing.
NOON_FIELDS = ("title", "price", "weight", "brand", "gtin", "description", "stock", "category")

_FIELD_TOKENS = {
    "title": {"title", "name"},
    "price": {"price", "aed"},
    "weight": {"weight"},
    "brand": {"brand"},
    "gtin": {"gtin", "barcode", "ean"},
    "description": {"description"},
    "stock": {"stock", "qty", "quantity"},
    "category": {"category"},
}
_OLD_TOKENS = {"old", "current", "existing"}
_NEW_TOKENS = {"new"}
_SKU_PRIORITY = ("partner_sku", "noon_sku", "sku")


@dataclass(frozen=True)
class ColumnRole:
    column: str
    field: str
    side: str  # "old" or "new"


def tokens(header: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", header.lower()) if part]


def field_for(header_tokens: list[str]) -> str | None:
    names = set(header_tokens)
    for field, keys in _FIELD_TOKENS.items():
        if names & keys:
            return field
    return None


def sku_column(headers: list[str]) -> str | None:
    ranked: list[tuple[int, str]] = []
    for header in headers:
        parts = tokens(header)
        if not parts or set(parts) & (_OLD_TOKENS | _NEW_TOKENS):
            continue
        joined = "_".join(parts)
        if joined in _SKU_PRIORITY:
            ranked.append((_SKU_PRIORITY.index(joined), header))
        elif "partner" in parts and "sku" in parts:
            ranked.append((0, header))
        elif "noon" in parts and "sku" in parts:
            ranked.append((1, header))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def classify_columns(headers: list[str]) -> list[ColumnRole]:
    roles: list[ColumnRole] = []
    for header in headers:
        parts = tokens(header)
        field = field_for(parts)
        if field is None:
            continue
        names = set(parts)
        if names & _NEW_TOKENS:
            roles.append(ColumnRole(header, field, "new"))
        elif names & _OLD_TOKENS:
            roles.append(ColumnRole(header, field, "old"))
    return roles


def category_column(headers: list[str]) -> str | None:
    """Current Noon category. It is resent as-is and has no old/new pair."""
    for header in headers:
        parts = tokens(header)
        if field_for(parts) == "category" and not (set(parts) & (_OLD_TOKENS | _NEW_TOKENS)):
            return header
    return None


def weight_unit(header: str) -> str:
    parts = set(tokens(header))
    if parts & {"g", "gram", "grams"}:
        return "g"
    return "kg"

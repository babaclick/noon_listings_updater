"""Write Noon title, price, and weight onto the matching pforge.listings row."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg

_UPDATE = """
UPDATE pforge.listings
SET
  final_product_name = CASE
    WHEN %(title)s::text IS NULL THEN final_product_name
    ELSE %(title)s::text
  END,
  listing_weight_kg_per_unit = CASE
    WHEN %(weight_kg)s::numeric IS NULL THEN listing_weight_kg_per_unit
    ELSE %(weight_kg)s::numeric
  END,
  listing_weight_kg_total = CASE
    WHEN %(weight_kg)s::numeric IS NULL THEN listing_weight_kg_total
    ELSE %(weight_kg)s::numeric
  END,
  schema_fill_json = CASE
    WHEN %(price)s::text IS NULL THEN with_title.json
    ELSE jsonb_set(
      with_title.json,
      '{noon_economics,sell_aed}',
      to_jsonb(%(price)s::text),
      true
    )
  END,
  updated_at = now()
FROM (
  SELECT CASE
    WHEN %(title)s::text IS NULL THEN COALESCE(schema_fill_json, '{}'::jsonb)
    ELSE jsonb_set(
      COALESCE(schema_fill_json, '{}'::jsonb),
      '{noon_title_en}',
      to_jsonb(%(title)s::text),
      true
    )
  END AS json
  FROM pforge.listings
  WHERE marketplace_id = 'noon_ae'
    AND sku = %(sku)s
) AS with_title
WHERE pforge.listings.marketplace_id = 'noon_ae'
  AND pforge.listings.sku = %(sku)s
"""


def check_connection(database_url: str) -> str:
    """Open the database, read pforge.listings, and close. No writes."""
    with psycopg.connect(database_url.strip(), connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            (name,) = cur.fetchone()
            cur.execute(
                """
                SELECT 1
                FROM pforge.listings
                WHERE marketplace_id = 'noon_ae'
                LIMIT 1
                """
            )
            cur.fetchone()
        conn.rollback()
    return f"Connected to {name}. Read pforge.listings."


def listing_values(changes: dict[str, Any]) -> dict[str, Any]:
    """Fields this catalog row can store. Weight comes in as grams."""
    title = str(changes.get("title") or "").strip() or None
    price = changes.get("price")
    weight_g = changes.get("weight_g")
    weight_kg = None
    if weight_g is not None:
        weight_kg = (Decimal(str(weight_g)) / Decimal("1000")).quantize(Decimal("0.0001"))
    price_text = None if price is None else f"{float(price):.2f}"
    return {"title": title, "price": price_text, "weight_kg": weight_kg}


def save_listing(conn: psycopg.Connection, partner_sku: str, changes: dict[str, Any]) -> str:
    """Update the noon_ae pforge listing for this partner SKU. Returns a short status."""
    values = listing_values(changes)
    if not any(values.values()):
        return "database unchanged"
    try:
        with conn.cursor() as cur:
            cur.execute(_UPDATE, {"sku": partner_sku, **values})
            updated = cur.rowcount
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return f"database save failed: {exc}"
    if updated == 0:
        return "database: no pforge listing for this SKU"
    return "database saved"

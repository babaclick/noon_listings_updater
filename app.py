"""Upload a spreadsheet and push old/new Noon listing fields for each partner SKU."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import psycopg

from catalog import check_connection, save_listing
from mapping import classify_columns, sku_column, weight_unit
from noon_client import NoonClient, apply_changes
from settings import (
    AUTH_ENV,
    DATABASE_ENV,
    DEFAULT_REDIRECT_URI,
    missing_auth_env,
    production_database_url,
    write_auth_secrets,
)

st.set_page_config(page_title="Noon listing update", layout="wide")
st.title("Noon listing update")

missing_auth = missing_auth_env()
if missing_auth or not production_database_url():
    st.error("Clerk sign-in and the production database are not configured.")
    st.write(
        "In the Clerk dashboard, open OAuth applications and create one for this app. "
        f"Set the redirect URI to {DEFAULT_REDIRECT_URI} and allow the openid, email, and profile scopes. "
        "Then put these variables in noon_listing_updater/.env and start with python run.py."
    )
    needed = list(missing_auth)
    if not production_database_url():
        needed.append(DATABASE_ENV)
    st.code("\n".join(dict.fromkeys([*AUTH_ENV, DATABASE_ENV])))
    if needed:
        st.write("Missing: " + ", ".join(needed))
    st.stop()

try:
    write_auth_secrets()
    logged_in = bool(st.user.is_logged_in)
except Exception:
    st.error(
        "The environment variables are set. Stop this server and start it again "
        "with python run.py so Clerk sign-in can load."
    )
    st.stop()

if not logged_in:
    st.subheader("Sign in")
    st.write("Sign in with Clerk before uploading a file or updating Noon.")
    if st.button("Sign in with Clerk"):
        st.login()
    st.caption("If sign-in does not start, stop the server and run python run.py.")
    st.stop()

signed_in_as = (
    st.user.get("email")
    or st.user.get("name")
    or st.user.get("preferred_username")
    or st.user.get("sub")
    or "Clerk user"
)
identity, sign_out = st.columns([6, 1])
identity.caption(f"Signed in as {signed_in_as}")
if sign_out.button("Sign out"):
    st.logout()

st.caption(
    "Columns whose name contains old or current are the existing Noon value. "
    "Columns whose name contains new are the value to send. "
    "Only title, price, weight, brand, GTIN, description, and stock are used. "
    "Every row needs a Noon partner SKU."
)

upload = st.file_uploader("Excel or CSV file", type=["xlsx", "xls", "csv"])
if upload is None:
    st.stop()

if upload.name.lower().endswith(".csv"):
    frame = pd.read_csv(upload, dtype=str)
else:
    frame = pd.read_excel(upload, dtype=str)
frame = frame.fillna("")
headers = [str(column) for column in frame.columns]
sku_name = sku_column(headers)
roles = classify_columns(headers)

st.subheader("File")
st.metric("Rows", len(frame))
if sku_name is None:
    st.error("No Noon partner SKU column. Add noon_sku, partner_sku, or sku.")
else:
    st.success(f"Partner SKU column: {sku_name}")

role_rows = [
    {"column": role.column, "noon_field": role.field, "use_as": "value to send" if role.side == "new" else "existing value"}
    for role in roles
]
st.subheader("Detected Noon columns")
if role_rows:
    st.dataframe(pd.DataFrame(role_rows), use_container_width=True, hide_index=True)
else:
    st.warning("No old/current or new columns matched a Noon listing field.")

new_by_field: dict[str, str] = {}
old_by_field: dict[str, str] = {}
for role in roles:
    target = new_by_field if role.side == "new" else old_by_field
    target.setdefault(role.field, role.column)

mapping_rows = []
for field in sorted(set(new_by_field) | set(old_by_field)):
    mapping_rows.append(
        {
            "noon_field": field,
            "existing_column": old_by_field.get(field, ""),
            "new_column": new_by_field.get(field, ""),
        }
    )
st.subheader("Existing to new mapping")
st.dataframe(pd.DataFrame(mapping_rows), use_container_width=True, hide_index=True)

preview = []
missing_sku = 0
for record in frame.to_dict(orient="records"):
    sku = str(record.get(sku_name, "")).strip() if sku_name else ""
    if not sku:
        missing_sku += 1
    for field, new_column in new_by_field.items():
        old_column = old_by_field.get(field, "")
        preview.append(
            {
                "partner_sku": sku,
                "noon_field": field,
                "existing": record.get(old_column, "") if old_column else "",
                "to_send": record.get(new_column, ""),
            }
        )
if missing_sku:
    st.error(f"{missing_sku} rows have no partner SKU and will not be sent.")
st.subheader("Row changes")
st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)


def _changes_for_row(record: dict, new_columns: dict[str, str]) -> dict:
    changes: dict = {}
    for field, column in new_columns.items():
        raw = str(record.get(column, "")).strip()
        if not raw:
            continue
        if field == "price":
            changes["price"] = float(raw)
        elif field == "weight":
            amount = float(raw)
            if weight_unit(column) == "kg":
                amount *= 1000
            changes["weight_g"] = round(amount, 1)
        elif field == "stock":
            changes["stock"] = int(float(raw))
        else:
            changes[field] = raw
    return changes


st.subheader("Production database")
database_url = production_database_url()
st.caption("Listing updates use BABACLICK_PROD_DATABASE_URL. The page does not take a database URL.")
if "db_ok" not in st.session_state:
    st.session_state.db_ok = False
if st.button("Test database connection", disabled=not database_url):
    try:
        st.session_state.db_check_message = check_connection(database_url)
        st.session_state.db_ok = True
    except Exception as exc:
        st.session_state.db_ok = False
        st.session_state.db_check_message = ""
        st.error(f"Database connection failed. {exc}")
db_ready = bool(database_url) and bool(st.session_state.db_ok)
if db_ready:
    st.success(st.session_state.get("db_check_message") or "Database connection succeeded.")
else:
    st.info("Test the database connection before updating Noon.")

st.subheader("Noon credentials")
key_id = st.text_input("Key id")
project_code = st.text_input("Project code", value="PRJ576169")
base_url = st.text_input("API base URL", value="https://noon-api-gateway.noon.partners")
private_key = st.text_area("Private key", height=160)
confirm = st.checkbox("Send these new values to Noon and save them on the production listing")

ready = bool(
    sku_name
    and new_by_field
    and private_key.strip()
    and key_id.strip()
    and db_ready
    and confirm
)
if st.button("Update Noon listings", disabled=not ready):
    progress = st.progress(0)
    status = st.empty()
    table = st.empty()
    results: list[dict[str, str]] = []
    records = frame.to_dict(orient="records")
    total = len(records)
    database = None
    client = None
    status.write("Testing database connection")
    try:
        try:
            status.write(check_connection(database_url))
            database = psycopg.connect(database_url.strip(), connect_timeout=15)
        except Exception as exc:
            st.session_state.db_ok = False
            st.error(f"Database connection failed. Noon was not contacted. {exc}")
            st.stop()
        client = NoonClient(
            private_key=private_key,
            key_id=key_id,
            project_code=project_code,
            base_url=base_url,
        )
        try:
            client.login()
        except Exception as exc:
            st.error(f"Noon login failed. No listings were changed. {exc}")
            st.stop()
        for index, record in enumerate(records, start=1):
            sku = str(record.get(sku_name, "")).strip()
            if not sku:
                results.append({"partner_sku": "", "status": "skipped", "detail": "missing partner SKU"})
            else:
                changes = _changes_for_row(record, new_by_field)
                if not changes:
                    results.append({"partner_sku": sku, "status": "skipped", "detail": "no new values"})
                else:
                    try:
                        calls = apply_changes(client, sku, changes)
                        failed = [call for call in calls if not call["ok"]]
                        detail = ", ".join(call["call"] for call in calls if call["ok"])
                        if failed:
                            detail = "; ".join(
                                [detail, *(f"{call['call']} HTTP {call['status_code']}" for call in failed)]
                            ).strip("; ")
                        if not failed:
                            detail = f"{detail}; {save_listing(database, sku, changes)}"
                            results.append({"partner_sku": sku, "status": "updated", "detail": detail})
                        else:
                            results.append({"partner_sku": sku, "status": "failed", "detail": detail})
                    except Exception as exc:
                        results.append({"partner_sku": sku, "status": "failed", "detail": str(exc)})
            progress.progress(index / total)
            status.write(f"{index} of {total} — {results[-1]['partner_sku']} — {results[-1]['status']}")
            table.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
    finally:
        if client is not None:
            client.close()
        if database is not None:
            database.close()
    updated = sum(1 for row in results if row["status"] == "updated")
    failed = sum(1 for row in results if row["status"] == "failed")
    st.write(f"Updated {updated}. Failed {failed}. Skipped {total - updated - failed}.")

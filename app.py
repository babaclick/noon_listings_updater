"""Upload a spreadsheet and push old/new Noon listing fields for each partner SKU."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import psycopg

from catalog import check_connection, save_listing
from clerk_auth import ClerkAuthError, clerk_user_label, revoke_session, session_is_active, verify_clerk_token
from mapping import category_column, classify_columns, sku_column, weight_unit
from noon_client import NoonClient, apply_changes
from settings import AUTH_ENV, DATABASE_ENV, missing_auth_env, production_database_url, sign_in_url

MARKETPLACE_ID = "noon_ae"

st.set_page_config(page_title="Noon listing update", layout="wide")
st.title("Noon listing update")

missing_auth = missing_auth_env()
if missing_auth or not production_database_url():
    st.error("Clerk sign-in and the production database are not configured.")
    st.write(
        "Use the same Clerk instance as Pforge. Copy CLERK_SECRET_KEY and CLERK_JWT_ISSUER "
        "from Pforge Backend, and CLERK_PUBLISHABLE_KEY from the frontend "
        "VITE_CLERK_PUBLISHABLE_KEY. On Render, set them as service environment variables. "
        "CLERK_AUTHORIZED_PARTIES is this service's public origin, such as "
        "https://your-service.onrender.com."
    )
    needed = list(missing_auth)
    if not production_database_url():
        needed.append(DATABASE_ENV)
    st.code("\n".join(dict.fromkeys([*AUTH_ENV, "CLERK_AUTHORIZED_PARTIES", DATABASE_ENV])))
    if needed:
        st.write("Missing: " + ", ".join(needed))
    st.stop()


def _query_value(name: str) -> str:
    raw = st.query_params.get(name, "")
    if isinstance(raw, (list, tuple)):
        raw = raw[-1] if raw else ""
    return str(raw or "").strip()


def _session_cookie() -> str:
    cookies = getattr(st.context, "cookies", {}) or {}
    named = {str(name): str(value).strip() for name, value in cookies.items()}
    if named.get("noon_clerk_token"):
        return named["noon_clerk_token"]
    for name, value in named.items():
        if name.startswith("__session") and value:
            return value
    return ""


def _establish_session(token: str) -> None:
    claims = verify_clerk_token(token)
    session_id = str(claims.get("sid") or "").strip()
    user_id = str(claims["sub"])
    st.session_state.clerk_user = {
        "sub": user_id,
        "sid": session_id,
        "label": clerk_user_label(user_id),
    }
    st.session_state.pop("clerk_error", None)


if _query_value("clerk_signed_out") == "1":
    current = st.session_state.get("clerk_user") or {}
    if current.get("sid"):
        try:
            revoke_session(str(current["sid"]))
        except ClerkAuthError as exc:
            st.session_state.clerk_error = str(exc)
    st.session_state.pop("clerk_user", None)
    del st.query_params["clerk_signed_out"]
    st.rerun()

presented = _query_value("clerk_token")
from_query = bool(presented)
if not presented and not st.session_state.get("clerk_user"):
    presented = _session_cookie()
if presented and from_query:
    try:
        _establish_session(presented)
    except ClerkAuthError as exc:
        st.session_state.pop("clerk_user", None)
        st.session_state.clerk_error = str(exc)
    del st.query_params["clerk_token"]
    st.rerun()
elif presented and not st.session_state.get("clerk_user"):
    try:
        _establish_session(presented)
    except ClerkAuthError as exc:
        if not exc.expired:
            st.session_state.clerk_error = str(exc)

clerk_user = st.session_state.get("clerk_user")
if clerk_user:
    session_id = str(clerk_user.get("sid") or "")
    if session_id:
        try:
            active = session_is_active(session_id)
        except ClerkAuthError as exc:
            st.error(str(exc))
            st.stop()
        if not active:
            st.session_state.pop("clerk_user", None)
            clerk_user = None

if not clerk_user:
    st.subheader("Sign in")
    st.write("Sign in with the same Clerk Google account as Pforge before uploading a file or updating Noon.")
    if st.session_state.get("clerk_error"):
        st.error(st.session_state.clerk_error)
    st.link_button("Sign in with Clerk", sign_in_url())
    st.stop()

identity, sign_out = st.columns([6, 1])
identity.caption(f"Signed in as {clerk_user.get('label') or clerk_user.get('sub')}")
sign_out.link_button("Sign out", sign_in_url() + "?sign_out=1")

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
category_name = category_column(headers)
roles = classify_columns(headers)

st.subheader("File")
st.metric("Rows", len(frame))
if sku_name is None:
    st.error("No Noon partner SKU column. Add noon_sku, partner_sku, or sku.")
else:
    st.success(f"Partner SKU column: {sku_name}")
if MARKETPLACE_ID.startswith("noon"):
    if category_name is None:
        st.error("Noon content updates need a noon_category column.")
    else:
        st.success(f"Noon category column: {category_name}")

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
    if category_name:
        category = str(record.get(category_name, "")).strip()
        if category:
            changes["category"] = category
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

content_fields = {"title", "weight", "brand", "description"}
category_required = MARKETPLACE_ID.startswith("noon") and bool(content_fields & set(new_by_field))
missing_category = 0
if category_required:
    for record in frame.to_dict(orient="records"):
        if not str(record.get(category_name or "", "")).strip():
            missing_category += 1
    if missing_category:
        st.error(f"{missing_category} Noon rows have no category and will not be sent.")

ready = bool(
    sku_name
    and new_by_field
    and private_key.strip()
    and key_id.strip()
    and db_ready
    and confirm
    and not missing_category
)
if "tested_sku" not in st.session_state:
    st.session_state.tested_sku = ""


def _result_detail(calls: list[dict], database_note: str = "") -> tuple[bool, str]:
    failed = [call for call in calls if not call["ok"]]
    parts = []
    for call in calls:
        if call["ok"]:
            parts.append(call["call"])
        else:
            parts.append(f"{call['call']} HTTP {call['status_code']}: {call.get('detail') or ''}")
    if database_note:
        parts.append(database_note)
    return not failed, "; ".join(parts)


def _run_records(records: list[dict]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    database = None
    client = None
    try:
        check_connection(database_url)
        database = psycopg.connect(database_url.strip(), connect_timeout=15)
        client = NoonClient(
            private_key=private_key,
            key_id=key_id,
            project_code=project_code,
            base_url=base_url,
        )
        client.login()
        for record in records:
            sku = str(record.get(sku_name, "")).strip()
            if not sku:
                results.append({"partner_sku": "", "status": "skipped", "detail": "missing partner SKU"})
                continue
            changes = _changes_for_row(record, new_by_field)
            if not any(key != "category" for key in changes):
                results.append({"partner_sku": sku, "status": "skipped", "detail": "no new values"})
                continue
            calls = apply_changes(client, sku, changes, marketplace_id=MARKETPLACE_ID)
            ok, detail = _result_detail(calls)
            if ok:
                detail = f"{detail}; {save_listing(database, sku, changes)}"
                results.append({"partner_sku": sku, "status": "updated", "detail": detail})
            else:
                results.append({"partner_sku": sku, "status": "failed", "detail": detail})
    finally:
        if client is not None:
            client.close()
        if database is not None:
            database.close()
    return results


first_record = next(
    (
        record
        for record in frame.to_dict(orient="records")
        if str(record.get(sku_name or "", "")).strip()
    ),
    None,
)
if st.button("Test one listing", disabled=not ready):
    try:
        test_results = _run_records([first_record])
    except Exception as exc:
        st.session_state.tested_sku = ""
        st.error(f"Test failed before the listing was changed. {exc}")
    else:
        st.session_state.tested_sku = test_results[0]["partner_sku"] if test_results[0]["status"] == "updated" else ""
        st.dataframe(pd.DataFrame(test_results), use_container_width=True, hide_index=True)
tested = bool(first_record) and st.session_state.tested_sku == str(first_record.get(sku_name, "")).strip()
if ready and not tested:
    st.info("Test one listing before updating the rest.")
if st.button("Update Noon listings", disabled=not (ready and tested)):
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
                if not any(key != "category" for key in changes):
                    results.append({"partner_sku": sku, "status": "skipped", "detail": "no new values"})
                else:
                    try:
                        calls = apply_changes(client, sku, changes, marketplace_id=MARKETPLACE_ID)
                        ok, detail = _result_detail(calls)
                        if ok:
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

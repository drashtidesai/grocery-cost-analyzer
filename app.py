from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from grocery.browser import URLS
from grocery.handoff import merge_noon_result, read_json
from browser_worker import atomic_json
from grocery.models import (STORES, GroceryItem, Product, calculate_purchase, format_quantity,
                            parse_quantity, read_csv, summarize, validate_rows)

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Dubai Grocery Compare", page_icon="🛒", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 2rem; max-width: 1440px;}
[data-testid="stMetric"] {background: white; border: 1px solid #dfe8e2; border-radius: 12px; padding: 16px;}
[data-testid="stTabs"] button {font-size: 1rem;}
.eyebrow {font-size: .8rem; letter-spacing: .16em; color: #147d64; font-weight: 700;}
</style>""", unsafe_allow_html=True)


def initialize() -> None:
    if "rows" not in st.session_state:
        sample = ROOT / "Grocery List.csv"
        st.session_state.rows = read_csv(sample.read_bytes()) if sample.exists() else []
        st.session_state.editor_version = 0
        st.session_state.run_id = "initial"
        st.session_state.result = None
        st.session_state.manual = {}
        st.session_state.current_rows = st.session_state.rows


def replace_rows(rows: list[dict]) -> None:
    st.session_state.rows = rows
    st.session_state.current_rows = rows
    st.session_state.editor_version += 1


def fingerprint(area: str, items: list[GroceryItem]) -> str:
    payload = json.dumps({"area": area.strip().casefold(), "items": [i.to_dict() for i in items]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def send_noon_command(job: dict, action: str, wait_id: str = "", confirmed_area: str | None = None) -> None:
    atomic_json(Path(job["control"]), {"action": action, "wait_id": wait_id, "confirmed_area": confirmed_area, "id": uuid4().hex})


def start_noon_job(area: str, items: list[GroceryItem], token: str) -> dict:
    directory = ROOT / ".runtime" / ("noon-" + uuid4().hex)
    directory.mkdir(parents=True, exist_ok=True)
    request, output, control = directory / "request.json", directory / "result.json", directory / "control.json"
    atomic_json(request, {"area": area, "items": [i.to_dict() for i in items], "stores": ["Noon Minutes"], "interactive": True})
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "browser_worker.py"), "--request", str(request),
                                    "--output", str(output), "--control", str(control)],
                                   cwd=ROOT, stdout=log, stderr=log, creationflags=creationflags)
    return {"token": token, "output": str(output), "control": str(control), "process": process}


@st.fragment(run_every=2)
def noon_handoff_panel(area: str, items: list[GroceryItem], token: str) -> None:
    job = st.session_state.get("noon_job")
    if job:
        if job["token"] != token:
            send_noon_command(job, "cancel")
            st.session_state.pop("noon_job", None)
            st.info("The list or area changed; the previous Noon browser session was cancelled.")
            return
        output = Path(job["output"])
        update = read_json(output)
        if update:
            previous = st.session_state.result if st.session_state.get("result_token") == token else None
            st.session_state.result = merge_noon_result(previous, update)
            st.session_state.result_token = token
            st.session_state.pop("noon_job", None)
            st.rerun()
        if job["process"].poll() is not None:
            st.session_state.pop("noon_job", None)
            st.error("The Noon browser session ended without results. Open a new session to retry.")
            return
        event = read_json(output.with_suffix(".progress.json"))
        st.markdown("**Noon guest browser session**")
        st.info(event.get("message", "Opening a Noon guest browser window…"))
        waiting = event.get("phase") in ("verification_required", "location_required")
        if waiting:
            st.caption("Use the separate browser window opened by the app on this computer. Complete the requested steps yourself; keep that window open and return here. No sign-in is needed or attempted.")
            confirmed_area = None
            if event.get("phase") == "location_required":
                if st.checkbox(f"I selected a delivery location in {area} in this Noon browser.", key=f"noon_area_{event.get('wait_id', '')}"):
                    confirmed_area = area
            if st.button("Continue Noon search", key="continue_noon"):
                send_noon_command(job, "continue", event.get("wait_id", ""), confirmed_area)
                st.toast("Checking the same Noon session…")
        else:
            st.progress(min(event.get("done", 0) / max(event.get("total", 1), 1), 1.0))
        if st.button("Cancel Noon browser session", key="cancel_noon"):
            send_noon_command(job, "cancel")
            st.session_state.pop("noon_job", None)
            st.rerun()
        return
    current = st.session_state.result if st.session_state.get("result_token") == token else None
    status = (current or {}).get("stores", {}).get("Noon Minutes", {}).get("status")
    if status in ("blocked", "verification_required", "location_required", "cancelled", "handoff_timeout", "search_unavailable", "error") and area and items:
        st.info("Noon needs your help to continue. Open its guest browser session, complete the human/robot check if shown, then continue the search here.")
        st.caption("The app keeps that browser session for the search. Opening Noon in an unrelated browser tab will not complete this session's verification. Your Amazon results remain available while you finish the check.")
        if st.button("Open Noon verification session", type="primary", key="open_noon_handoff"):
            try:
                st.session_state.noon_job = start_noon_job(area, items, token)
                st.rerun()
            except OSError as exc:
                st.error(f"Could not open the Noon browser: {exc}")


def run_search(area: str, items: list[GroceryItem]) -> dict:
    directory = ROOT / ".runtime" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    request_path, output = directory / "request.json", directory / "result.json"
    request_path.write_text(json.dumps({"area": area, "items": [i.to_dict() for i in items]}), encoding="utf-8")
    progress_path = output.with_suffix(".progress.json")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with st.status("Searching both stores as a guest…", expanded=True) as status:
        message, bar = st.empty(), st.progress(0)
        with (directory / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "browser_worker.py"), "--request", str(request_path), "--output", str(output)],
                                       cwd=ROOT, stdout=log, stderr=log, creationflags=creationflags)
            started = time.monotonic()
            while process.poll() is None:
                if progress_path.exists():
                    try:
                        event = json.loads(progress_path.read_text(encoding="utf-8"))
                        message.write(event["message"])
                        bar.progress(min(event.get("done", 0) / max(event.get("total", 1), 1), 1.0))
                    except (ValueError, OSError):
                        pass
                if time.monotonic() - started > min(1800, 150 + 40 * len(items)):
                    process.terminate()
                    process.wait(timeout=10)
                    status.update(label="Search timed out", state="error")
                    return {"error": "The price collector timed out. Try fewer items or retry later."}
                time.sleep(0.3)
        if not output.exists():
            status.update(label="Could not start browser collection", state="error")
            return {"error": "Browser collection failed to start. Run uv run playwright install chromium, then retry."}
        result = json.loads(output.read_text(encoding="utf-8"))
        bar.progress(1.0)
        has_problem = "error" in result or any(s.get("status") != "ready" for s in result.get("stores", {}).values())
        status.update(label="Search finished — review store status below" if has_problem else "Search finished — choose your products", state="error" if has_problem else "complete", expanded=False)
        return result


def local_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y, %H:%M GST")
    except ValueError:
        return value


def candidates_for(item: GroceryItem, store: str, result: dict | None, token: str) -> list[Product]:
    raw = (result or {}).get("products", {}).get(f"{item.id}|{store}", {}).get("candidates", [])
    manual = st.session_state.manual.get(f"{token}|{item.id}|{store}", [])
    return [Product(**row) for row in raw + manual]


def candidate_record(item: GroceryItem, product: Product) -> dict:
    row = {"Product": product.name, "Pack size": product.pack_size or "Unknown — cannot total",
           "Pack price (AED)": float(product.price), "Availability": "Available" if product.available else "Unavailable",
           "Match": product.match, "Source": product.source, "Checked": local_time(product.retrieved_at), "Product link": product.url}
    try:
        purchase = calculate_purchase(item, product)
        row.update({"Packs needed": purchase.packs, "Item total (AED)": float(purchase.total),
                    "Purchased quantity": format_quantity(purchase.bought, purchase.dimension),
                    "Excess": format_quantity(purchase.excess, purchase.dimension),
                    "Unit price (AED)": float(purchase.unit_price)})
    except (ValueError, ArithmeticError) as exc:
        row["Match"] += f" · {exc}"
    return row


initialize()
st.markdown('<div class="eyebrow">DUBAI · GROCERY PRICE COMPARISON</div>', unsafe_allow_html=True)
st.title("Your grocery list. Two stores. One clear choice.")
st.caption("Compare Amazon Now and Noon Minutes in AED, using enough whole packs for your list.")

with st.sidebar:
    st.header("Dubai Grocery Compare")
    st.write("Guest shopping · AED")
    st.link_button("Open Amazon Now ↗", URLS["Amazon Now"], width="stretch")
    st.link_button("Open Noon Minutes ↗", URLS["Noon Minutes"], width="stretch")
    st.caption("These links open the public storefronts. A retailer may request a location, verification, or sign-in; this app never signs in.")
    st.divider()
    st.write("**How totals work**")
    st.caption("Choose a product at each store. We round pack counts up and show any extra quantity. Different brands are alternatives for you to approve.")
    st.caption("Totals include product prices only. Delivery and service fees, checkout discounts, membership deals and promotions requiring conditions are excluded.")

input_tab, prices_tab, compare_tab, totals_tab = st.tabs(["1 · Grocery Input", "2 · Store Prices", "3 · Item Comparison", "4 · Basket Totals"])

with input_tab:
    st.subheader("Where are you shopping?")
    area = st.text_input("Dubai delivery area", placeholder="e.g. Al Barsha 1, Dubai Marina, Jumeirah Village Circle", max_chars=120, key="delivery_area").strip()
    st.caption("Enter an area rather than a full home address. The area and grocery search terms are sent to both retailers when you search.")
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.subheader("Upload your list")
        uploaded = st.file_uploader("CSV with Item name and Weight columns", type=["csv"])
        if uploaded is not None and st.button("Use uploaded CSV"):
            try:
                replace_rows(read_csv(uploaded.getvalue()))
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        st.download_button("Download CSV template", "Item name,Weight\nPotato,1kg\nFull Cream Milk Al Rawabi,2 Litres\n", "grocery-template.csv", "text/csv")
    with right:
        st.subheader("Add an item")
        with st.form("manual_item", clear_on_submit=True):
            name = st.text_input("Item name", placeholder="Full Cream Milk Al Rawabi", max_chars=160)
            weight = st.text_input("Weight / volume / count", placeholder="2 litres")
            add = st.form_submit_button("Add to grocery list")
        if add:
            new_rows, new_errors = validate_rows([{"Item name": name, "Weight": weight}])
            if new_errors or not new_rows:
                st.error(" ".join(new_errors) or "Enter an item name and quantity.")
            else:
                replace_rows(st.session_state.current_rows + [{"Item name": name.strip(), "Weight": weight.strip()}])
                st.rerun()
    st.subheader("Your grocery list")
    st.caption("Your provided Grocery List.csv is loaded initially. Edit rows below; select rows to remove them, or add rows directly.")
    frame = pd.DataFrame(st.session_state.rows, columns=["Item name", "Weight"]).fillna("").astype(str)
    edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch",
                            key=f"grocery_editor_{st.session_state.editor_version}",
                            column_config={"Item name": st.column_config.TextColumn(required=True), "Weight": st.column_config.TextColumn(required=True)})
    # Persist the current table so adding a manual row does not discard table edits.
    st.session_state.current_rows = edited.fillna("").to_dict("records")
    items, errors = validate_rows(st.session_state.current_rows)
    for error in errors:
        st.error(error)
    if any(parse_quantity(i.weight).is_range for i in items):
        st.info("Requested weight ranges use the upper end: 200–250g is treated as 250g. Variable-weight product packs use their lower end.")
    m1, m2 = st.columns(2)
    m1.metric("Grocery items", len(items))
    m2.metric("Delivery area", area or "Enter an area")
    st.download_button("Export current grocery list", edited.to_csv(index=False), "Grocery List.csv", "text/csv")
    search = st.button("Search both stores", type="primary", disabled=not area or not items or bool(errors), width="stretch")

token = fingerprint(area, items)
if search:
    if st.session_state.get("noon_job"):
        send_noon_command(st.session_state.pop("noon_job"), "cancel")
    st.session_state.result = run_search(area, items)
    st.session_state.result_token = token
    st.session_state.run_id = uuid4().hex

result = st.session_state.result if st.session_state.get("result_token") == token else None
selected: dict[tuple[str, str], Product] = {}
has_manual = any(key.startswith(token + "|") for key in st.session_state.manual)

with prices_tab:
    st.subheader("Review store prices and choose products")
    st.caption("Every candidate needs your confirmation. Compare names, brand, fat level, variety and pack size before selecting.")
    noon_handoff_panel(area, items, token)
    if st.session_state.result and result is None:
        st.warning("Your list or area changed. Previous search results are excluded; search again for the updated list.")
    if result and result.get("error"):
        st.error(result["error"])
    if result:
        store_columns = st.columns(2)
        for column, store in zip(store_columns, STORES):
            with column:
                status = result.get("stores", {}).get(store, {})
                st.markdown(f"**{store}**")
                if status.get("status") == "ready":
                    label = "Delivery area confirmed by you" if status.get("area_confirmation") == "user" else "Guest area confirmed"
                    st.success(f"{label}: {status.get('confirmed_area', area)}")
                else:
                    st.warning(status.get("message", "No results returned."))
        st.caption(f"Search started: {local_time(result.get('retrieved_at', ''))}. Prices and availability can change before checkout.")
    if not result and not has_manual:
        st.info("Enter your delivery area and click Search both stores in tab 1.")

    if area and items and not errors:
        with st.expander("Add a manually checked guest price — fallback when a store blocks access"):
            st.caption("Only enter a displayed pack price checked for the same delivery area. Manual entries are clearly labelled and can be removed below.")
            with st.form("manual_price", clear_on_submit=True):
                a, b = st.columns(2)
                target_id = a.selectbox("Grocery item", [i.id for i in items], format_func=lambda x: next(f"{i.name} · {i.weight}" for i in items if i.id == x))
                target_store = b.selectbox("Store", list(STORES))
                product_name = st.text_input("Actual product name and brand")
                c, d = st.columns(2)
                pack_size = c.text_input("Pack size", placeholder="500g or 6 x 200ml")
                price = d.number_input("Displayed pack price (AED)", min_value=0.01, max_value=99999.0, value=None, step=0.01)
                product_url = st.text_input("Product URL (optional)")
                verified = st.checkbox("I checked this available product and guest price for the delivery area above.")
                save_price = st.form_submit_button("Add manual alternative")
            if save_price:
                try:
                    if not product_name.strip() or not verified:
                        raise ValueError("Enter a product name and confirm the guest price and delivery area.")
                    if price is None:
                        raise ValueError("Enter the displayed guest pack price in AED.")
                    if product_url.strip():
                        parsed = urlparse(product_url.strip())
                        if parsed.scheme != "https" or parsed.hostname != urlparse(URLS[target_store]).hostname:
                            raise ValueError("Use an HTTPS product link from the selected store, or leave it blank.")
                    product = Product(uuid4().hex, target_store, product_name.strip(), pack_size.strip(), str(price), product_url.strip(), True,
                                      datetime.now(timezone.utc).isoformat(), area, "Manually checked alternative", source="Manual guest price")
                    calculate_purchase(next(i for i in items if i.id == target_id), product)
                    manual_key = f"{token}|{target_id}|{target_store}"
                    st.session_state.manual.setdefault(manual_key, []).append(product.to_dict())
                    st.rerun()
                except (ValueError, ArithmeticError) as exc:
                    st.error(str(exc))
            if has_manual and st.button("Remove manual prices for this list and area"):
                st.session_state.manual = {key: value for key, value in st.session_state.manual.items() if not key.startswith(token + "|")}
                st.session_state.run_id = uuid4().hex
                st.rerun()

    if result or has_manual:
        st.subheader("Prices at a glance")
        st.caption("Top search candidates are suggestions until you select them below; pack prices here are not basket totals.")
        for column, store in zip(st.columns(2, gap="large"), STORES):
            with column:
                st.markdown(f"**{store}**")
                overview = []
                for item in items:
                    candidates = candidates_for(item, store, result, token)
                    top = candidates[0] if candidates else None
                    overview.append({"Grocery item": item.name, "Requested": item.weight,
                                     "Suggested product": top.name if top else "No guest price",
                                     "Pack size": top.pack_size if top else "—",
                                     "Pack price (AED)": float(top.price) if top else None,
                                     "Source": top.source if top else "—"})
                st.dataframe(pd.DataFrame(overview), hide_index=True, width="stretch")
        st.subheader("Choose your products")
        for item in items:
            with st.expander(f"{item.name} · requested {item.weight}", expanded=len(items) <= 3):
                for column, store in zip(st.columns(2, gap="large"), STORES):
                    with column:
                        st.markdown(f"**{store}**")
                        candidates = candidates_for(item, store, result, token)
                        if not candidates:
                            item_result = (result or {}).get("products", {}).get(f"{item.id}|{store}", {})
                            st.caption(item_result.get("message", "No guest-priced candidates available."))
                            continue
                        st.dataframe(pd.DataFrame([candidate_record(item, p) for p in candidates]), hide_index=True, width="stretch",
                                     column_config={"Product link": st.column_config.LinkColumn("Product link", display_text="View product")})
                        usable = []
                        for product in candidates:
                            try:
                                calculate_purchase(item, product)
                                usable.append(product)
                            except (ValueError, ArithmeticError):
                                pass
                        options = [None] + [p.id for p in usable]
                        def label(pid, products=usable, grocery_item=item):
                            if pid is None:
                                return "Choose a product / leave missing"
                            p = next(p for p in products if p.id == pid)
                            purchase = calculate_purchase(grocery_item, p)
                            return f"{p.name} · {p.pack_size} · {purchase.packs} pack(s) · AED {purchase.total:.2f} · {p.source}"
                        choice = st.selectbox(f"Selected product · {store}", options, format_func=label,
                                              key=f"choice_{token}_{st.session_state.run_id}_{item.id}_{store}")
                        if choice:
                            selected[(item.id, store)] = next(p for p in usable if p.id == choice)
                        if candidates and not usable:
                            st.caption("Candidates lack a compatible pack size or availability. Check the product and add a manual entry if appropriate.")

comparison = []
details = []
for item in items:
    row = {"Item": item.name, "Requested": item.weight}
    costs = {}
    for store in STORES:
        product = selected.get((item.id, store))
        row[f"{store} (AED)"] = None
        if product:
            purchase = calculate_purchase(item, product)
            costs[store] = purchase.total
            row[f"{store} (AED)"] = float(purchase.total)
            details.append({"Item": item.name, "Store": store, "Product": product.name,
                            "Pack size": product.pack_size, "Packs": purchase.packs,
                            "Pack price (AED)": float(product.price), "Total (AED)": float(purchase.total),
                            "Purchased": format_quantity(purchase.bought, purchase.dimension),
                            "Excess": format_quantity(purchase.excess, purchase.dimension),
                            "Unit price (AED)": float(purchase.unit_price),
                            "Unit basis": "each" if purchase.dimension == "count" else "kg" if purchase.dimension == "mass" else "L",
                            "Source": product.source, "Delivery area": area, "Checked": local_time(product.retrieved_at), "Product URL": product.url})
    if len(costs) == 2:
        row["Cheaper option"] = "Tie" if len(set(costs.values())) == 1 else min(costs, key=costs.get)
        row["Savings (AED)"] = float(abs(costs[STORES[0]] - costs[STORES[1]]))
    else:
        row["Cheaper option"] = "Missing selection"
        row["Savings (AED)"] = None
    comparison.append(row)


def color_comparison(row):
    styles = [""] * len(row)
    first, second = (f"{store} (AED)" for store in STORES)
    if pd.notna(row[first]) and pd.notna(row[second]) and row[first] != row[second]:
        for col in (first, second):
            cheaper = row[col] == min(row[first], row[second])
            styles[row.index.get_loc(col)] = "background-color: #dcf3e5; color: #105c35" if cheaper else "background-color: #ffe1df; color: #982b24"
    return styles


with compare_tab:
    st.subheader("Compare your selected products")
    st.caption("Green = cheaper · Red = costlier · Ties and missing prices remain neutral. Colours compare whole-pack item totals.")
    if comparison:
        comparison_frame = pd.DataFrame(comparison)
        numeric = [f"{s} (AED)" for s in STORES] + ["Savings (AED)"]
        st.dataframe(comparison_frame.style.apply(color_comparison, axis=1).format({c: "{:.2f}" for c in numeric}, na_rep="—"), hide_index=True, width="stretch")
    if details:
        st.subheader("Pack counts and quantity details")
        st.dataframe(pd.DataFrame(details), hide_index=True, width="stretch",
                     column_config={"Product URL": st.column_config.LinkColumn(display_text="View product")})
        st.download_button("Download comparison CSV", pd.DataFrame(details).to_csv(index=False), "grocery-comparison.csv", "text/csv")
        if any(p.source.startswith("Manual") for p in selected.values()):
            st.caption("This comparison includes manually entered guest prices. See the Source column.")
    else:
        st.info("Choose products in Store Prices to build your comparison.")

with totals_tab:
    st.subheader("Which store costs less for your whole list?")
    summary = summarize(items, selected)
    for col, store in zip(st.columns(2), STORES):
        with col:
            count = summary["coverage"][store]
            st.metric(f"{store} · {'Full basket' if count == len(items) and items else 'Selected-item subtotal'}", f"AED {summary['totals'][store]:.2f}" if count else "—")
            st.caption(f"{count} of {len(items)} items selected" + (" · incomplete basket" if count < len(items) else ""))
    if summary["complete"]:
        if summary["winner"] == "Tie":
            st.success("Both stores have the same whole-list product total.")
        else:
            saving = abs(summary["totals"][STORES[0]] - summary["totals"][STORES[1]])
            st.success(f"{summary['winner']} is cheaper overall for your selected products. Save AED {saving:.2f}.")
        st.caption("The result uses your selected alternatives and whole-pack quantities. Delivery and service fees are excluded.")
    else:
        st.info("A whole-list winner needs a valid selection for every item at both stores. Missing prices are not counted as zero.")
        missing = [{"Store": store, "Missing items": ", ".join(i.name for i in items if (i.id, store) not in selected)} for store in STORES]
        if items:
            st.dataframe(pd.DataFrame(missing), hide_index=True, width="stretch")
        if summary["common_count"]:
            st.write(f"**Like-for-like subtotal: {summary['common_count']} items selected at both stores**")
            st.write(" · ".join(f"{s}: AED {summary['common'][s]:.2f}" for s in STORES))
            st.caption("This covers shared selected items only and does not establish the cheaper store for the whole list.")

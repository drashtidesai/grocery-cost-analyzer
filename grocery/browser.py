"""Guest-only browser collection. Run in browser_worker.py, never Streamlit's thread."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Page, TimeoutError as BrowserTimeout, sync_playwright

from grocery.matching import broader_query, rank_products, search_query
from grocery.models import GroceryItem, STORES
from grocery.parsing import product_from_card

URLS = {
    "Amazon Now": "https://www.amazon.ae/alm/storefront/yalla?almBrandId=sAuWWBROaG&ref=yalla_dsk_sn_logo-4d9ec",
    "Noon Minutes": "https://minutes.noon.com/uae-en/",
}


class StoreUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_area(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def area_matches(requested: str, displayed: str) -> bool:
    req = normalize_area(requested)
    shown = normalize_area(displayed)
    # The whole area must be visible; a truncated label does not prove the location.
    return bool(req) and req in shown and "..." not in displayed and "…" not in displayed


def choose_area_label(requested: str, labels: list[str]) -> str | None:
    """Allow a single more-specific official label; never fuzzy-match neighbourhoods."""
    request = normalize_area(re.sub(r",\s*Dubai\s*$", "", requested, flags=re.I))
    exact = [label for label in labels if normalize_area(label) == request]
    if len(exact) == 1:
        return exact[0]
    specific = [label for label in labels if normalize_area(label).startswith(request + " ")]
    return specific[0] if len(specific) == 1 else None


def is_now_search(url: str) -> bool:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    index = params.get("i") == ["amazonyalla"] or params.get("s") == ["amazonyalla"]
    return parsed.hostname == "www.amazon.ae" and index and bool(params.get("srs"))


def check_access(page: Page, status: int | None = None) -> None:
    text = page.locator("body").inner_text(timeout=10000)[:14000].lower()
    if any(marker in text for marker in ("performing security verification", "verify you are human",
                                         "enter the characters you see below", "robot check", "captcha",
                                         "sorry, we just need to make sure you're not a robot")):
        raise StoreUnavailable("verification_required", "The store requires a human security check before guest searches can continue.")
    if status in (403, 429, 503):
        raise StoreUnavailable("blocked", f"The store refused guest access (HTTP {status}). Try again later.")
    if re.search(r"/(?:ap/signin|login|signin)(?:[/?]|$)", page.url):
        raise StoreUnavailable("login_required", "The store redirected to sign-in. No login was attempted.")


def first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(min(locator.count(), 12)):
            if locator.nth(index).is_visible():
                return locator.nth(index)
    return None


def set_amazon_area(page: Page, area: str) -> str:
    link = page.locator("#nav-global-location-popover-link")
    if not link.count():
        raise StoreUnavailable("location_required", "Amazon's guest delivery-area control could not be found.")
    link.click()
    # Wait for the location UI, then work only with visible guest inputs.
    page.wait_for_timeout(1500)
    check_access(page)
    field = first_visible(page, [
        "#GLUXCityWithDistrict_DistrictInput", "#GLUXCityList", "#GLUXAreaList", "input[placeholder*='area' i]",
        "input[placeholder*='location' i]", "input[placeholder*='Search' i]:not(#twotabsearchtextbox)",
        "input[placeholder*='address' i]", "input[role='combobox']",
    ])
    if field is None:
        # Some regional UIs expose city/area as buttons that open a searchable list.
        choice = page.get_by_text(re.compile(r"select (?:an? )?(?:area|city)|choose (?:an? )?(?:area|city)", re.I))
        if choice.count() and choice.first.is_visible():
            choice.first.click()
            field = first_visible(page, ["input[placeholder*='Search' i]:not(#twotabsearchtextbox)", "input[type='text']:not(#twotabsearchtextbox)"])
    selected_label = area
    if field is not None:
        if field.evaluate("e => e.tagName") == "SELECT":
            options = field.locator("option").all_text_contents()
            matches = [label for label in options if area_matches(area, label)]
            if len(matches) == 1:
                field.select_option(label=matches[0])
        else:
            field.fill(area)
            page.wait_for_timeout(1500)
            list_items = page.locator("#GLUXCityWithDistrict_DistrictList li")
            labels = list_items.all_text_contents()
            selected_label = choose_area_label(area, labels)
            if not selected_label:
                suggestions = [label for label in labels if normalize_area(area) in normalize_area(label)][:5]
                hint = f" Try a precise store area: {', '.join(suggestions)}." if suggestions else " Try the store's full area name."
                raise StoreUnavailable("location_required", "Amazon could not uniquely match your delivery area." + hint)
            option = list_items.filter(has_text=re.compile(rf"^\s*{re.escape(selected_label)}\s*$", re.I))
            visible = [option.nth(i) for i in range(option.count()) if option.nth(i).is_visible()]
            if len(visible) == 1:
                visible[0].click()
    apply = first_visible(page, ["#GLUXCityWithDistrictApplyButton input", "#GLUXZipUpdate input"])
    if apply is not None:
        apply.click()
    page.keyboard.press("Escape")
    # Read back the saved selection on a fresh page, not an uncommitted text field.
    page.reload(wait_until="domcontentloaded", timeout=35000)
    page.wait_for_timeout(1500)
    check_access(page)
    # Full tooltip, aria-label or visible label can verify the area, never a default.
    shown = " ".join(filter(None, [link.inner_text(), link.get_attribute("aria-label"), link.get_attribute("title")]))
    if not area_matches(area, shown):
        # Amazon truncates the header. Reopen the guest selector to read its full saved area.
        link.click()
        page.wait_for_timeout(1000)
        district = first_visible(page, ["#GLUXCityWithDistrict_DistrictInput"])
        city = first_visible(page, ["#GLUXCityWithDistrictCityInput"])
        saved_area = district.input_value() if district is not None else ""
        saved_city = city.input_value() if city is not None else ""
        page.keyboard.press("Escape")
        if normalize_area(saved_city) == "dubai" and normalize_area(saved_area) == normalize_area(selected_label):
            shown = f"Dubai, {saved_area}"
        else:
            shown = ""
    if not area_matches(re.sub(r",\s*Dubai\s*$", "", area, flags=re.I), shown):
        raise StoreUnavailable("location_required", f"Amazon did not confirm the requested area '{area}'. No prices were used for a default location.")
    return shown


def set_noon_area(page: Page, area: str) -> str:
    # Guest storefront UI only; do not call private APIs or supply guessed coordinates.
    confirmed = confirmed_noon_area(page, area)
    if confirmed:
        return confirmed
    location = page.get_by_role("button", name=re.compile(r"deliver|location|address", re.I))
    direct_location = first_visible(page, ["button[class*='_addressButton_']"])
    if direct_location is not None:
        direct_location.click()
    elif location.count() and location.first.is_visible():
        location.first.click()
    field = first_visible(page, ["input[placeholder*='area' i]", "input[placeholder*='location' i]",
                                 "input[placeholder*='address' i]"])
    if field is not None:
        field.fill(area + ", Dubai")
        page.wait_for_timeout(1500)
        suggestions = page.get_by_text(re.compile(rf"^\s*{re.escape(area)}(?:\s*,.*)?$", re.I))
        visible = [suggestions.nth(i) for i in range(suggestions.count()) if suggestions.nth(i).is_visible()]
        if len(visible) == 1:
            visible[0].click()
            confirm = page.get_by_role("button", name=re.compile(r"^(?:Confirm location|Deliver here|Use this location|Confirm)$", re.I))
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
    page.wait_for_timeout(1000)
    check_access(page)
    shown = confirmed_noon_area(page, area)
    if not shown:
        raise StoreUnavailable("location_required", f"Noon did not confirm the requested area '{area}'. No prices were used for a default location.")
    return shown


def confirmed_noon_area(page: Page, area: str) -> str:
    labels = page.locator("header, [data-testid*='location'], [data-testid*='address'], [class*='_addressText_']").all_text_contents()
    return next((label for label in labels if area_matches(area, label)), "")


# Extract the displayed pack price, avoiding list prices and prices per kg/litre.
AMAZON_CARDS = r"""() => [...document.querySelectorAll('[data-component-type="s-search-result"]')].map(card => {
  const title = card.querySelector('h2');
  const link = title?.closest('a') || title?.querySelector('a') || card.querySelector('a.a-link-normal.s-no-outline');
  const price = card.querySelector('.a-price:not(.a-text-price)');
  return {name: title?.textContent?.trim(), url: link?.getAttribute('href'),
          price: price?.querySelector('.a-offscreen')?.textContent?.trim(), text: card.innerText};
})"""

NOON_CARDS = r"""() => [...document.querySelectorAll('a[href]')].filter(a => /\/(?:p|product|now-product)\/|\/N[A-Z0-9]+\/p\//i.test(a.getAttribute('href'))).map(a => {
  const card = a.closest('[data-testid*="product"], [class*="productBox"], [class*="ProductCard"]') || a;
  const title = card.querySelector('[data-testid*="title"], [class*="productTitle"], [class*="ProductTitle"], h2, h3') || card.querySelector('img[alt]');
  const price = card.querySelector('[data-testid="product-price"] [class*="sellingPrice"] strong, [data-testid="price"], [class*="sellingPrice"] strong, [class*="SellingPrice"] strong, [class*="salePrice"]');
  const size = card.querySelector('[class*="sizeInfo"], [data-testid="product-size"]');
  return {name: title?.textContent?.trim() || title?.getAttribute('alt'), url:a.getAttribute('href'),
          price:price?.textContent?.trim(), pack_size:size?.textContent?.trim(), text:card.innerText};
})"""


def search_cards(page: Page, store: str, query: str) -> list[dict]:
    field = first_visible(page, ["#twotabsearchtextbox"] if store == "Amazon Now" else [
        "[data-testid='search-input-form'] input",
        "input[type='search']", "input[placeholder*='search' i]", "input[aria-label*='search' i]",
    ])
    if field is None:
        raise StoreUnavailable("search_unavailable", "The guest search field could not be found. Store layout may have changed.")
    if store == "Amazon Now":
        # Use the public search route and the merchant filter observed on the storefront.
        # This avoids a delayed location-refresh racing with the first form submission.
        options = page.locator("#searchDropdownBox option").evaluate_all("els => els.map(e => e.value)")
        now = next((parse_qs(value) for value in options if "search-alias=amazonyalla" in value), {})
        merchant = now.get("srs") or parse_qs(urlparse(page.url).query).get("srs")
        if not merchant:
            raise StoreUnavailable("search_unavailable", "The Now storefront merchant filter could not be verified.")
        url = "https://www.amazon.ae/s?" + urlencode({"k": query, "i": "amazonyalla", "srs": merchant[0]})
        response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
        check_access(page, response.status if response else None)
    else:
        field.fill(query)
        field.press("Enter")
    page.wait_for_timeout(2200)
    check_access(page)
    if store == "Amazon Now":
        # The Now search must retain its merchant/store filter, never normal Amazon marketplace results.
        if not is_now_search(page.url):
            raise StoreUnavailable("search_unavailable", "Amazon search left the Now storefront. Marketplace prices were excluded.")
        try:
            page.locator('[data-component-type="s-search-result"]').first.wait_for(timeout=8000)
        except BrowserTimeout:
            return []
        return [card for card in page.evaluate(AMAZON_CARDS) if re.search(r"Sold by:\s*Amazon\s*Now", card.get("text", ""), re.I)]
    if urlparse(page.url).hostname != "minutes.noon.com":
        raise StoreUnavailable("search_unavailable", "Noon search left the Minutes storefront.")
    return page.evaluate(NOON_CARDS)


def collect_prices(request: dict, progress: Callable[[dict], None] = lambda _: None,
                   human_control: Callable[[Page, str, str], str | None] | None = None) -> dict:
    area = str(request.get("area", "")).strip()
    if not area or len(area) > 120:
        raise ValueError("Enter a Dubai delivery area of at most 120 characters.")
    items = [GroceryItem(**row) for row in request.get("items", [])]
    if not 1 <= len(items) <= 100:
        raise ValueError("Provide between 1 and 100 grocery items.")
    stores = request.get("stores", list(STORES))
    if not stores or any(store not in STORES for store in stores):
        raise ValueError("Select a supported grocery store.")
    interactive = request.get("interactive") is True
    if interactive and stores != ["Noon Minutes"]:
        raise ValueError("Interactive handoff is available for a Noon-only search.")

    def with_human_check(action, page, store):
        while True:
            try:
                return action()
            except StoreUnavailable as exc:
                if exc.code not in ("verification_required", "blocked") or store != "Noon Minutes" or human_control is None:
                    raise
                human_control(page, store, "verification_required")
                # Retain this page/context. The user's successful check belongs to this session.
    result = {"area": area, "stores": {}, "products": {}, "items": [i.to_dict() for i in items],
              "retrieved_at": datetime.now(timezone.utc).isoformat()}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not interactive)
        try:
            for store in stores:
                context = browser.new_context(locale="en-GB", timezone_id="Asia/Dubai", viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.set_default_timeout(12000)
                progress({"message": f"Opening {store} as a guest…", "done": 0, "total": len(items)})
                try:
                    response = page.goto(URLS[store], wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(3000)
                    try:
                        check_access(page, response.status if response else None)
                    except StoreUnavailable as exc:
                        if exc.code not in ("verification_required", "blocked") or store != "Noon Minutes" or human_control is None:
                            raise
                        human_control(page, store, "verification_required")
                        check_access(page)  # Original HTTP 403 is stale after successful human verification.
                    area_confirmation = "store"
                    if store == "Amazon Now":
                        confirmed = set_amazon_area(page, area)
                    else:
                        try:
                            confirmed = with_human_check(lambda: set_noon_area(page, area), page, store)
                        except (StoreUnavailable, BrowserTimeout) as exc:
                            if human_control is None or (isinstance(exc, StoreUnavailable) and exc.code != "location_required"):
                                raise
                            confirmed = confirmed_noon_area(page, area)
                            while not confirmed:
                                attested = human_control(page, store, "location_required")
                                confirmed = confirmed_noon_area(page, area)
                                if not confirmed and attested and normalize_area(attested) == normalize_area(area):
                                    confirmed = area
                                    area_confirmation = "user"
                    result["stores"][store] = {"status": "ready", "message": "Guest location confirmed", "confirmed_area": confirmed,
                                               "area_confirmation": area_confirmation}
                    for index, item in enumerate(items):
                        progress({"message": f"{store}: searching {item.name}", "done": index, "total": len(items)})
                        try:
                            query = search_query(item.name)
                            cards = with_human_check(lambda: search_cards(page, store, query), page, store)
                            broad = broader_query(item.name)
                            if broad.casefold() != query.casefold():
                                cards += with_human_check(lambda: search_cards(page, store, broad), page, store)
                            products = {}
                            for card in cards:
                                product = product_from_card(card, store, URLS[store], area, datetime.now(timezone.utc).isoformat())
                                if product:
                                    products[product.id] = product
                            ranked = rank_products(item, list(products.values()))
                            result["products"][f"{item.id}|{store}"] = {
                                "status": "found" if ranked else "not_found",
                                "message": "Confirm a product below." if ranked else "No guest-priced product was found.",
                                "candidates": [product.to_dict() for product in ranked],
                            }
                        except StoreUnavailable as exc:
                            result["products"][f"{item.id}|{store}"] = {"status": exc.code, "message": str(exc), "candidates": []}
                            if exc.code in ("blocked", "verification_required", "login_required", "cancelled", "handoff_timeout"):
                                result["stores"][store] = {"status": exc.code, "message": str(exc)}
                                break
                        except BrowserTimeout:
                            result["products"][f"{item.id}|{store}"] = {"status": "timeout", "message": "Search timed out. Please retry.", "candidates": []}
                    progress({"message": f"Finished {store}", "done": len(items), "total": len(items)})
                except StoreUnavailable as exc:
                    result["stores"][store] = {"status": exc.code, "message": str(exc)}
                except Exception as exc:
                    result["stores"][store] = {"status": "error", "message": f"Guest browsing failed: {str(exc)[:240]}"}
                finally:
                    context.close()
        finally:
            browser.close()
    return result

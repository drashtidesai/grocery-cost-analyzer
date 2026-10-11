"""Development probe: inspect guest storefront markup without using an account."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

URLS = {
    "amazon": "https://www.amazon.ae/alm/storefront/yalla?almBrandId=sAuWWBROaG",
    "noon": "https://minutes.noon.com/uae-en/",
}

if __name__ == "__main__":
    target = Path(".runtime/probe")
    target.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for name, url in URLS.items():
            page = browser.new_page(locale="en-GB", viewport={"width": 1440, "height": 1000})
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(6000)
                text = page.locator("body").inner_text(timeout=10000)
                (target / f"{name}.txt").write_text(text, encoding="utf-8")
                (target / f"{name}.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(target / f"{name}.png"), full_page=False)
                print(json.dumps({"store": name, "status": response.status if response else None,
                                  "url": page.url, "text": text[:1200],
                                  "inputs": page.locator("input").evaluate_all("els => els.map(e => ({id:e.id, placeholder:e.placeholder, type:e.type, name:e.name}))")}, ensure_ascii=False))
                if name == "amazon":
                    page.locator("#nav-global-location-popover-link").click()
                    page.wait_for_timeout(2500)
                    print(json.dumps({"store": name, "stage": "location", "text": page.locator("body").inner_text()[-1600:]}), flush=True)
                    (target / "amazon-location.html").write_text(page.content(), encoding="utf-8")
                    district = page.locator("#GLUXCityWithDistrict_DistrictInput")
                    district.fill("Al Barsha 1")
                    page.wait_for_timeout(2500)
                    print(json.dumps({"stage": "area-options", "text": page.locator("#GLUXCityWithDistrict_DistrictList").inner_text(),
                                      "options": page.locator("#GLUXCityWithDistrict_DistrictList li").all_text_contents()}), flush=True)
                    (target / "amazon-area.html").write_text(page.content(), encoding="utf-8")
                    option = page.locator("#GLUXCityWithDistrict_DistrictList li").filter(has_text="Al Barsha 1")
                    if option.count() and option.first.is_visible():
                        option.first.click()
                    page.locator("#GLUXCityWithDistrictApplyButton input").click()
                    page.wait_for_timeout(3000)
                    print(json.dumps({"stage": "area-applied", "text": page.locator("body").inner_text()[-2000:],
                                      "header": page.locator("#nav-global-location-popover-link").inner_text()}), flush=True)
                    page.keyboard.press("Escape")
                    page.locator("#twotabsearchtextbox").fill("Potato")
                    page.locator("#twotabsearchtextbox").press("Enter")
                    page.wait_for_timeout(5000)
                    print(json.dumps({"store": name, "stage": "search", "url": page.url, "text": page.locator("body").inner_text()[:2500]}), flush=True)
                    (target / "amazon-search.html").write_text(page.content(), encoding="utf-8")
            except Exception as exc:
                print(json.dumps({"store": name, "error": str(exc)}))
            finally:
                page.close()
        browser.close()

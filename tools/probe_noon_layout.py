"""Inspect a guest page after the visible browser reaches a user handoff; never solve a check."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grocery.browser import StoreUnavailable
from playwright.sync_api import sync_playwright


def inspect(page, store, phase):
    destination = Path(".runtime/noon-layout")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "page.html").write_text(page.content(), encoding="utf-8")
    text = page.locator("body").inner_text()
    (destination / "page.txt").write_text(text, encoding="utf-8")
    controls = page.locator("input, button, header, [role='button']").evaluate_all("els => els.map(e => ({tag:e.tagName, text:e.innerText, placeholder:e.getAttribute('placeholder'), testid:e.getAttribute('data-testid'), aria:e.getAttribute('aria-label'), cls:e.className}))")
    (destination / "controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"phase": phase, "url": page.url, "text": text[:6000], "controls": controls[:45]}, ensure_ascii=True), flush=True)
    raise StoreUnavailable("cancelled", "Read-only layout inspection complete.")


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            context = browser.new_context(locale="en-GB", timezone_id="Asia/Dubai", viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            response = page.goto("https://minutes.noon.com/uae-en/", wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3000)
            print(json.dumps({"http_status": response.status if response else None}), flush=True)
            inspect(page, "Noon Minutes", "guest_page")
        except StoreUnavailable:
            pass
        finally:
            browser.close()

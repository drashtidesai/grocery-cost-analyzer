from playwright.sync_api import sync_playwright

from grocery.browser import NOON_CARDS
from grocery.parsing import product_from_card


def test_current_noon_markup_uses_selling_price_and_separate_pack_size():
    # Structure observed on the actual guest Noon storefront: quantity is outside the title,
    # the currency is an icon, and an old price follows the selling price.
    markup = """<a data-testid="catalog-product-box-Z275603F5702206631905Z-1"
        href="/uae-en/now-product/Z275603F5702206631905Z-1/">
      <h2 data-testid="product-title">MLK Classic Vanilla Full Fat Milk</h2>
      <span class="_sizeInfo_yvp4r_93">300ml</span>
      <div data-testid="product-price">
        <div class="_sellingPrice_1u9no_15"><span>\ue001</span><strong>3</strong></div>
        <div class="_preReductionPrice_1u9no_69"><span>7.50</span></div>
      </div></a>"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(markup)
            cards = page.evaluate(NOON_CARDS)
        finally:
            browser.close()
    assert len(cards) == 1
    product = product_from_card(cards[0], "Noon Minutes", "https://minutes.noon.com/uae-en/", "Dubai Marina", "2026-10-11")
    assert product.price == "3.00"
    assert product.pack_size == "300ml"
    assert product.name == "MLK Classic Vanilla Full Fat Milk"

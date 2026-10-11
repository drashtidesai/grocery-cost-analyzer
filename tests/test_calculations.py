from decimal import Decimal
from pathlib import Path

import pytest

from grocery.browser import area_matches, choose_area_label, is_now_search
from grocery.models import GroceryItem, Product, calculate_purchase, parse_quantity, read_csv, summarize, validate_rows
from grocery.parsing import extract_pack_size, parse_displayed_price, product_from_card
from grocery.matching import relevant_product, rank_products, search_query


def product(store="Amazon Now", size="500g", price="7.25", available=True):
    return Product("p1", store, "Test food", size, price, "https://example.com/product", available, "2026-10-11T00:00:00+00:00", "Al Barsha 1")


@pytest.mark.parametrize("text,amount,dimension", [
    ("1kg", "1000", "mass"), ("200-250g", "250", "mass"),
    ("2 Litres", "2000", "volume"), ("6 x 200ml", "1200", "volume"),
    ("200ml x 6", "1200", "volume"), ("6 pcs", "6", "count"),
    ("0.9-1 kg", "1000", "mass"), ("500g-1kg", "1000", "mass"),
])
def test_quantities(text, amount, dimension):
    parsed = parse_quantity(text)
    assert parsed.amount == Decimal(amount)
    assert parsed.dimension == dimension


@pytest.mark.parametrize("text", ["", "0g", "-10g", "250", "one kg", "200ml-1kg", "500-250g", "2.5 pcs", "250g garbage"])
def test_invalid_quantities(text):
    with pytest.raises(ValueError):
        parse_quantity(text)


def test_whole_packs_and_excess():
    purchase = calculate_purchase(GroceryItem("0", "Paneer", "750g"), product())
    assert (purchase.packs, purchase.total, purchase.bought, purchase.excess) == (2, Decimal("14.50"), Decimal(1000), Decimal(250))
    assert purchase.unit_price == Decimal("14.50")


def test_exact_pack_and_multipacks():
    assert calculate_purchase(GroceryItem("0", "Milk", "2 litres"), product(size="2L")).packs == 1
    purchase = calculate_purchase(GroceryItem("0", "Milk", "2 litres"), product(size="6 x 200ml"))
    assert purchase.packs == 2
    assert purchase.excess == 400


def test_variable_product_weight_uses_guaranteed_minimum():
    purchase = calculate_purchase(GroceryItem("0", "Potato", "1kg"), product(size="900-1000g"))
    assert purchase.packs == 2
    assert purchase.bought == 1800


def test_incompatible_or_unavailable_products():
    item = GroceryItem("0", "Milk", "2L")
    with pytest.raises(ValueError):
        calculate_purchase(item, product())
    with pytest.raises(ValueError):
        calculate_purchase(item, product(size="2L", available=False))


def test_missing_prices_do_not_make_a_store_cheaper():
    items = [GroceryItem("0", "Paneer", "750g"), GroceryItem("1", "Paneer", "500g")]
    selected = {("0", "Amazon Now"): product(), ("1", "Amazon Now"): product(),
                ("0", "Noon Minutes"): product("Noon Minutes", price="6.00")}
    report = summarize(items, selected)
    assert report["winner"] is None
    assert report["coverage"] == {"Amazon Now": 2, "Noon Minutes": 1}
    assert report["common"] == {"Amazon Now": Decimal("14.50"), "Noon Minutes": Decimal("12.00")}
    selected[("1", "Noon Minutes")] = product("Noon Minutes", price="6.00")
    assert summarize(items, selected)["winner"] == "Noon Minutes"


def test_ties_and_empty_basket():
    item = GroceryItem("0", "Food", "500g")
    assert summarize([item], {("0", store): product(store) for store in ("Amazon Now", "Noon Minutes")})["winner"] == "Tie"
    assert summarize([], {})["winner"] is None


def test_users_csv_and_bom():
    rows = read_csv(Path("Grocery List.csv").read_bytes())
    items, errors = validate_rows(rows)
    assert len(items) == 10 and errors == []
    assert read_csv(b"\xef\xbb\xbfItem name,Weight\nPotato,1kg\n")[0]["Weight"] == "1kg"
    with pytest.raises(ValueError):
        read_csv(b"Name,Price\nMilk,10\n")


@pytest.mark.parametrize("name,size", [
    ("Fresh Potato 900-1000g, Import 4-7 Pcs", "900-1000g"),
    ("Milk 6 x 200ml", "6 x 200ml"), ("Milk 200ml x 6", "200ml x 6"),
    ("Water 1.5 litres (Pack of 6)", "1.5 litres (Pack of 6)"),
    ("Greek Yogurt 0% Fat, 160g", "160g"), ("Spinach", ""),
])
def test_pack_extraction(name, size):
    assert extract_pack_size(name) == size


def test_price_and_product_parser():
    assert parse_displayed_price("AED\u00a05.50") == "5.50"
    assert parse_displayed_price("AED 1,200.75") == "1200.75"
    assert parse_displayed_price("No price") is None
    card = {"name": "Potato 1kg", "price": "AED 5.50", "url": "/dp/example", "text": "Sold by AmazonNow"}
    p = product_from_card(card, "Amazon Now", "https://www.amazon.ae/", "Al Barsha 1", "2026-10-11")
    assert p.price == "5.50" and p.pack_size == "1kg"
    assert product_from_card({**card, "text": "Prime exclusive price"}, "Amazon Now", "https://www.amazon.ae/", "Al Barsha 1", "2026-10-11") is None
    assert product_from_card({**card, "url": "https://evil.example/product"}, "Amazon Now", "https://www.amazon.ae/", "Al Barsha 1", "2026-10-11") is None


def test_area_must_be_confirmed_without_truncation():
    assert area_matches("Al Barsha 1", "Dubai, Al Barsha 1")
    assert not area_matches("Al Barsha 1", "Dubai, Al B...")
    assert not area_matches("Dubai Marina", "Dubai, Al Barsha 1")


def test_area_labels_cannot_silently_choose_a_different_district():
    assert choose_area_label("Al Barsha 1", ["Al Barsha 1 Mall Of The Emirates", "Al Barsha 2"]) == "Al Barsha 1 Mall Of The Emirates"
    assert choose_area_label("Dubai Marina", ["Dubai Marina", "Dubai Maritime City"]) == "Dubai Marina"
    assert choose_area_label("Jumeirah Village Circle", ["Jumeirah Village Circle District 10", "Jumeirah Village Circle District 11"]) is None
    assert choose_area_label("Al Barsha 1", ["Al Barsha 10"]) is None


def test_now_filter_excludes_marketplace_results():
    assert is_now_search("https://www.amazon.ae/s?k=Potato&i=amazonyalla&srs=100704208031")
    assert is_now_search("https://www.amazon.ae/s?k=Potato&s=amazonyalla&srs=100704208031")
    assert not is_now_search("https://www.amazon.ae/s?k=Potato")
    assert not is_now_search("https://www.amazon.ae/s?k=Potato&i=aps&srs=100704208031")
    assert not is_now_search("https://example.com/s?k=Potato&i=amazonyalla&srs=100704208031")


def test_produce_does_not_match_flavoured_snacks():
    cherries = GroceryItem("0", "Cherries", "250g")
    sweets = product()
    sweets.name = "Haribo Happy Cherries, 80 Gm"
    fresh = product(size="250g")
    fresh.name = "Fresh Cherries 250g"
    assert not relevant_product(cherries, sweets)
    assert rank_products(cherries, [sweets, fresh]) == [fresh]
    chips = product()
    chips.name = "Potato Chips 500g"
    assert not relevant_product(GroceryItem("0", "Potato", "1kg"), chips)
    assert relevant_product(GroceryItem("0", "Potato Chips", "500g"), chips)
    balm = product(size="4.8g")
    balm.name = "Sebamed Lip Defense Cherry 4.8g"
    assert not relevant_product(cherries, balm)
    assert search_query("Cherries") == "fresh Cherries"
    assert search_query("Potato Chips") == "Potato Chips"
    tomato = product(size="250g")
    tomato.name = "Fresh Cherry Tomatoes 250g"
    assert not relevant_product(cherries, tomato)
    assert relevant_product(GroceryItem("0", "Tomato", "500g"), tomato)


def test_same_match_prefers_less_excess_and_lower_whole_pack_cost():
    item = GroceryItem("0", "Nada Greek Yogurt Plain", "160g")
    larger = product(size="360g", price="9.86")
    larger.name = "Nada Greek Yoghurt Plain 360g"
    exact = product(size="160g", price="5.74")
    exact.name = "Nada Greek Yoghurt Plain 160g"
    assert rank_products(item, [larger, exact])[0] is exact
    potato = GroceryItem("0", "Potato", "1kg")
    small = product(size="230-250g", price="5.01")
    small.name = "Fresh Potato Baby Premium 230-250g"
    regular = product(size="900-1000g", price="5.50")
    regular.name = "Fresh Potato 900-1000g"
    assert rank_products(potato, [small, regular])[0] is regular

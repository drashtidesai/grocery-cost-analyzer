"""Conservative parsers for displayed product cards; no invented prices."""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

from grocery.models import Product, parse_quantity, MEASURE, UNITS


def extract_pack_size(name: str) -> str:
    matches = list(re.finditer(MEASURE, name.lower().replace("×", "x")))
    if not matches:
        return ""
    measured = [m for m in matches if UNITS[m.group(4) or m.group(6)][0] != "count"]
    # Fresh produce often includes both '900-1000g' and '4-7 pcs'. Use its weight.
    match = (measured or matches)[-1]
    start, end = match.span()
    prefix = re.search(r"\d+\s*x\s*$", name[:start], re.I)
    if prefix:
        start = prefix.start()
    suffix = re.match(r"\s*(?:x\s*\d+|\(?\s*pack\s+of\s+\d+\)?)", name[end:], re.I)
    if suffix:
        end += suffix.end()
    size = name[start:end].strip()
    try:
        parse_quantity(size, product=True)
    except ValueError:
        return ""
    return size


def parse_displayed_price(text: str) -> str | None:
    cleaned = text.replace(",", "").replace("\ufffd", " ").strip()
    match = re.search(r"(?:AED|د\.إ\.?|Dhs\.?)\s*(\d+(?:\.\d{1,2})?)", cleaned, re.I)
    if not match:
        match = re.fullmatch(r"(?:AED\s*)?(\d+(?:\.\d{1,2})?)", cleaned, re.I)
    if not match:
        return None
    try:
        price = Decimal(match.group(1))
        return str(price.quantize(Decimal("0.01"))) if 0 < price < 100000 else None
    except InvalidOperation:
        return None


def product_from_card(card: dict, store: str, base_url: str, area: str, at: str) -> Product | None:
    name = str(card.get("name", "")).strip()
    raw_price = str(card.get("price", "")).strip()
    text = str(card.get("text", ""))
    # Membership, per-unit and crossed-out prices must not masquerade as guest pack prices.
    if not name or not raw_price or re.search(r"prime exclusive|member.only|sign in to see", text, re.I):
        return None
    price = parse_displayed_price(raw_price)
    if price is None:
        return None
    url = urljoin(base_url, str(card.get("url", "")))
    if urlparse(url).hostname != urlparse(base_url).hostname or not card.get("url"):
        return None
    if store == "Amazon Now":
        asin = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", url)
        if asin:
            url = f"https://www.amazon.ae/dp/{asin.group(1)}?almBrandId=sAuWWBROaG"
    pack_size = str(card.get("pack_size", "")).strip() or extract_pack_size(name)
    available = not bool(re.search(r"out of stock|unavailable|sold out|currently unavailable", text, re.I))
    return Product(hashlib.sha256((store + urlparse(url).path + name).encode()).hexdigest()[:20], store, name,
                   pack_size, price, url, available, at, area)

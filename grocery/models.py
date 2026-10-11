from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any

STORES = ("Amazon Now", "Noon Minutes")
UNITS = {
    "g": ("mass", Decimal(1)), "gram": ("mass", Decimal(1)),
    "grams": ("mass", Decimal(1)), "gm": ("mass", Decimal(1)), "gms": ("mass", Decimal(1)),
    "kg": ("mass", Decimal(1000)), "kgs": ("mass", Decimal(1000)),
    "kilogram": ("mass", Decimal(1000)), "kilograms": ("mass", Decimal(1000)),
    "ml": ("volume", Decimal(1)), "millilitre": ("volume", Decimal(1)),
    "millilitres": ("volume", Decimal(1)),
    "l": ("volume", Decimal(1000)), "ltr": ("volume", Decimal(1000)),
    "litre": ("volume", Decimal(1000)), "litres": ("volume", Decimal(1000)),
    "liter": ("volume", Decimal(1000)), "liters": ("volume", Decimal(1000)),
    "pc": ("count", Decimal(1)), "pcs": ("count", Decimal(1)),
    "piece": ("count", Decimal(1)), "pieces": ("count", Decimal(1)),
    "each": ("count", Decimal(1)), "unit": ("count", Decimal(1)),
    "units": ("count", Decimal(1)), "pack": ("count", Decimal(1)),
    "packs": ("count", Decimal(1)),
    "count": ("count", Decimal(1)), "counts": ("count", Decimal(1)),
}
UNIT_RE = "|".join(sorted(UNITS, key=len, reverse=True))
NUMBER = r"\d+(?:\.\d+)?"
MEASURE = rf"({NUMBER})(?:\s*({UNIT_RE}))?\s*[-–]\s*({NUMBER})\s*({UNIT_RE})\b|({NUMBER})\s*({UNIT_RE})\b"


@dataclass(frozen=True)
class Quantity:
    amount: Decimal
    dimension: str
    is_range: bool = False


def parse_quantity(text: str, *, product: bool = False) -> Quantity:
    """Base units are grams, millilitres, or pieces. Request ranges use their upper end.

    Product ranges use their lower end so whole packs never promise too little.
    """
    value = str(text).strip().lower().replace("×", "x")
    matches = list(re.finditer(MEASURE, value))
    if not matches:
        raise ValueError("Use a quantity such as 250g, 1kg, 2 litres, or 6 pcs.")
    match = matches[-1]
    low, low_unit, high, high_unit, single, single_unit = match.groups()
    is_range = low is not None
    if is_range:
        low_dimension, low_factor = UNITS[low_unit or high_unit]
        dimension, factor = UNITS[high_unit]
        if low_dimension != dimension:
            raise ValueError("A quantity range must use compatible units.")
        lower, upper = Decimal(low) * low_factor, Decimal(high) * factor
        if lower > upper:
            raise ValueError("The range minimum must be smaller than its maximum.")
        amount = lower if product else upper
    else:
        dimension, factor = UNITS[single_unit]
        amount = Decimal(single) * factor
    # Multipack: 6 x 200 ml; 200 ml x 6; 200 ml (pack of 6).
    prefix = value[:match.start()]
    suffix = value[match.end():]
    multiplier = re.search(r"(\d+)\s*x\s*$", prefix)
    trailing = re.match(r"\s*x\s*(\d+)\b", suffix)
    pack_of = re.search(r"\bpack\s+of\s+(\d+)\b", value)
    selected = multiplier or trailing or pack_of
    if selected:
        amount *= Decimal(selected.group(1))
    if amount <= 0 or amount > Decimal("100000000"):
        raise ValueError("Quantity must be positive and within a reasonable range.")
    if not product:
        # Reject unrelated numbers/units and malformed strings rather than quietly guessing.
        accepted = re.fullmatch(rf"\s*(?:{NUMBER}\s*x\s*)?(?:{MEASURE})(?:\s*x\s*\d+)?\s*", value)
        if not accepted:
            raise ValueError("Enter only the quantity, for example 200-250g or 2 litres.")
    if dimension == "count" and amount != amount.to_integral_value():
        raise ValueError("Piece counts must be whole numbers.")
    return Quantity(amount, dimension, is_range)


def format_quantity(amount: Decimal, dimension: str) -> str:
    if dimension in ("mass", "volume") and amount >= 1000:
        return f"{amount / 1000:g} {'kg' if dimension == 'mass' else 'L'}"
    unit = {"mass": "g", "volume": "ml", "count": "pcs"}[dimension]
    return f"{amount:g} {unit}"


@dataclass(frozen=True)
class GroceryItem:
    id: str
    name: str
    weight: str

    def to_dict(self) -> dict:
        return asdict(self)


def validate_rows(rows: list[dict]) -> tuple[list[GroceryItem], list[str]]:
    items, errors = [], []
    for index, row in enumerate(rows):
        name = str(row.get("Item name", "") or "").strip()
        weight = str(row.get("Weight", "") or "").strip()
        if not name and not weight:
            continue
        if not name or name.lower() == "nan":
            errors.append(f"Row {index + 1}: an item name is required.")
            continue
        if len(name) > 160:
            errors.append(f"Row {index + 1}: item name must be at most 160 characters.")
            continue
        try:
            parse_quantity(weight)
        except ValueError as exc:
            errors.append(f"Row {index + 1} ({name}): {exc}")
            continue
        items.append(GroceryItem(str(index), name, weight))
    if len(items) > 100:
        errors.append("Please compare at most 100 items per search.")
    return items, errors


def read_csv(data: bytes) -> list[dict[str, str]]:
    if len(data) > 1_000_000:
        raise ValueError("CSV must be smaller than 1 MB.")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        headers = {str(h).strip().casefold(): h for h in (reader.fieldnames or [])}
        if not {"item name", "weight"} <= headers.keys():
            raise ValueError("CSV needs the columns Item name and Weight.")
        rows = [{"Item name": row.get(headers["item name"], "") or "",
                 "Weight": row.get(headers["weight"], "") or ""} for row in reader]
        if len(rows) > 100:
            raise ValueError("CSV can contain at most 100 grocery rows.")
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Please upload a valid UTF-8 CSV file.") from exc


@dataclass
class Product:
    id: str
    store: str
    name: str
    pack_size: str
    price: str
    url: str
    available: bool
    retrieved_at: str
    area: str
    match: str = "Review match"
    score: float = 0
    source: str = "Live guest search"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Purchase:
    packs: int
    total: Decimal
    bought: Decimal
    excess: Decimal
    dimension: str
    unit_price: Decimal


def calculate_purchase(item: GroceryItem, product: Product) -> Purchase:
    if not product.available:
        raise ValueError("Product is unavailable.")
    requested = parse_quantity(item.weight)
    pack = parse_quantity(product.pack_size, product=True)
    if requested.dimension != pack.dimension:
        raise ValueError("Requested quantity and pack size use different units.")
    price = Decimal(product.price)
    if not price.is_finite() or price <= 0:
        raise ValueError("A valid positive pack price is required.")
    packs = int((requested.amount / pack.amount).to_integral_value(rounding=ROUND_CEILING))
    bought = pack.amount * packs
    scale = Decimal(1) if pack.dimension == "count" else Decimal(1000)
    return Purchase(packs, (price * packs).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                    bought, bought - requested.amount, pack.dimension,
                    (price / pack.amount * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def summarize(items: list[GroceryItem], selected: dict[tuple[str, str], Product]) -> dict:
    totals = {store: Decimal(0) for store in STORES}
    coverage = {store: 0 for store in STORES}
    common = {store: Decimal(0) for store in STORES}
    common_count = 0
    for item in items:
        costs = {}
        for store in STORES:
            product = selected.get((item.id, store))
            if product:
                try:
                    cost = calculate_purchase(item, product).total
                except (ValueError, ArithmeticError):
                    continue
                totals[store] += cost
                coverage[store] += 1
                costs[store] = cost
        if len(costs) == len(STORES):
            common_count += 1
            for store, cost in costs.items():
                common[store] += cost
    complete = bool(items) and all(c == len(items) for c in coverage.values())
    winner = None
    if complete:
        winner = "Tie" if totals[STORES[0]] == totals[STORES[1]] else min(totals, key=totals.get)
    return dict(totals=totals, coverage=coverage, common=common, common_count=common_count,
                complete=complete, winner=winner)

from __future__ import annotations

import re
from rapidfuzz.fuzz import token_set_ratio

from grocery.models import GroceryItem, Product, calculate_purchase, parse_quantity

ALIASES = {"yogurt": "yoghurt", "porotein": "protein", "strawberries": "strawberry",
           "potatoes": "potato", "tomatoes": "tomato", "bananas": "banana", "cherry": "cherries"}
PRODUCE = {"banana", "strawberry", "cherries", "potato", "tomato", "spinach"}
PROCESSED = {"chips", "crisps", "fries", "fried", "candy", "candies", "gummy", "gummies", "haribo", "chocolate",
             "yoghurt", "yogurt", "juice", "syrup", "jam", "jelly", "cake", "biscuits", "cookies", "sauce", "seasoning",
             "powder", "dried", "granola", "puree", "mashed", "muffin", "muffins", "ice", "drink", "beverage"}
NONFOOD = {"lip", "lipstick", "balm", "skin", "face", "shampoo", "soap", "cosmetics", "perfume",
           "hair", "conditioner", "lotion", "sunscreen", "scrub", "mask", "cleaner", "freshener"}


def normalized_tokens(text: str) -> set[str]:
    return {ALIASES.get(word, word) for word in re.sub(r"[^\w\s%]", " ", text.lower()).split()}


def relevant_product(item: GroceryItem, product: Product) -> bool:
    requested, candidate = normalized_tokens(item.name), normalized_tokens(product.name)
    # A cherry-flavoured sweet or potato crisp is not a different brand of fresh produce.
    categories = requested & PRODUCE
    if categories and not (requested & PROCESSED):
        candidate_categories = candidate & PRODUCE
        if "tomato" in candidate_categories:
            candidate_categories.discard("cherries")  # 'cherry tomatoes' are tomatoes, not cherries.
        if not categories <= candidate_categories or (candidate & (PROCESSED | NONFOOD)):
            return False
        if product.store != "Noon Minutes" and not (candidate & {"fresh", "organic"}):
            return False
        if "fresh" in requested and "frozen" in candidate:
            return False
    return True


def rank_products(item: GroceryItem, products: list[Product]) -> list[Product]:
    tokens = normalized_tokens(item.name)
    requested = " ".join(sorted(tokens))
    products = [product for product in products if relevant_product(item, product)]
    for product in products:
        candidate_tokens = normalized_tokens(product.name)
        candidate = " ".join(sorted(candidate_tokens))
        product.score = round(token_set_ratio(requested, candidate), 1)
        product.match = "Suggested match · confirm product" if tokens <= candidate_tokens else "Alternative · check brand and attributes"
        try:
            calculate_purchase(item, product)
        except (ValueError, ArithmeticError):
            product.score -= 30
    # Suggestions never silently select a different brand, fat level, variety, or pack.
    def preference(product):
        try:
            purchase = calculate_purchase(item, product)
            requested_amount = parse_quantity(item.weight).amount
            pack_amount = parse_quantity(product.pack_size, product=True).amount
            pack_fit = -abs(pack_amount - requested_amount) / requested_amount
            return (product.available, product.score, pack_fit, -purchase.total, -purchase.excess)
        except (ValueError, ArithmeticError):
            return (False, product.score, -float("inf"), -float("inf"), -float("inf"))
    return sorted(products, key=preference, reverse=True)[:8]


def search_query(name: str) -> str:
    tokens = normalized_tokens(name)
    if tokens & PRODUCE and not (tokens & PROCESSED) and "fresh" not in tokens:
        return "fresh " + name
    return name


def broader_query(name: str) -> str:
    """Keep the food category when a branded search produces no candidates."""
    lower = name.lower()
    categories = ("greek yogurt", "full cream milk", "chana dal", "paneer", "spinach", "strawberry",
                  "strawberries", "potato", "tomato", "cherries", "banana", "yogurt", "milk")
    for category in categories:
        if category in lower:
            return search_query(category)
    return name

"""Supermarket category lookup (deterministic, no LLM).

Implements the "Extraction (supermarket)" box: map an ingredient / grocery-item
name to the aisle it lives in (produce, meat, dairy, pantry, frozen, ...), so
the grocery list can be grouped for an efficient shop.

Resolution order:
  1. exact name hit in the category map (from the ingredient DB once it exists,
     else the inline placeholder)
  2. keyword rules (substring match)
  3. ``"other"``

Swapping in the real data source is a one-line change: point ``_load_category_map``
at ``ingredients_db.json`` (each ingredient there is expected to carry a
``"category"`` field). Until that file lands, ``PLACEHOLDER_CATEGORY_MAP`` is used.

Usage:
    >>> get_category("chicken breast")
    'meat'
    >>> get_category("frozen peas")
    'frozen'
    >>> get_category("something weird")
    'other'
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.schemas import GroceryItem, GroceryList

INGREDIENTS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ingredients_db.json"

KNOWN_CATEGORIES = (
    "produce", "meat", "seafood", "dairy", "bakery",
    "pantry", "frozen", "beverages", "condiments", "other",
)

PLACEHOLDER_CATEGORY_MAP: dict[str, str] = {
    "chicken breast": "meat", "chicken thigh": "meat", "beef mince": "meat", "pork loin": "meat",
    "salmon": "seafood", "prawns": "seafood", "white fish": "seafood",
    "firm tofu": "produce", "eggs": "dairy", "milk": "dairy", "greek yogurt": "dairy",
    "cheddar": "dairy", "butter": "dairy",
    "broccoli": "produce", "spinach": "produce", "carrot": "produce", "onion": "produce",
    "garlic": "produce", "tomato": "produce", "bell pepper": "produce", "potato": "produce",
    "white rice": "pantry", "brown rice": "pantry", "rolled oats": "pantry",
    "dried lentils": "pantry", "pasta": "pantry", "olive oil": "pantry", "soy sauce": "condiments",
    "canned chickpeas": "pantry", "canned tomatoes": "pantry",
    "frozen peas": "frozen", "frozen berries": "frozen", "bread": "bakery",
}

# (keywords, category) — first matching rule wins.
_KEYWORD_RULES: list[tuple[tuple[str, ...], str]] = [
    (("frozen",), "frozen"),
    (("canned", "tinned", "jar of"), "pantry"),
    (("chicken", "beef", "pork", "lamb", "mince", "bacon", "sausage"), "meat"),
    (("salmon", "tuna", "prawn", "shrimp", "fish", "squid", "mussel"), "seafood"),
    (("milk", "yogurt", "yoghurt", "cheese", "butter", "cream", "egg"), "dairy"),
    (("rice", "pasta", "noodle", "flour", "oat", "lentil", "bean", "oil", "sugar", "salt", "spice", "stock"), "pantry"),
    (("sauce", "ketchup", "mustard", "vinegar", "mayo", "dressing"), "condiments"),
    (("bread", "bun", "bagel", "tortilla", "wrap"), "bakery"),
    (("juice", "soda", "water", "tea", "coffee"), "beverages"),
    (("lettuce", "spinach", "kale", "tomato", "onion", "garlic", "pepper", "carrot",
      "potato", "broccoli", "cucumber", "apple", "banana", "lemon", "lime", "herb", "tofu"), "produce"),
]

_map_cache: dict[str, str] | None = None


def _load_category_map(path: Path = INGREDIENTS_DB_PATH, *, use_cache: bool = True) -> dict[str, str]:
    """Category map from the ingredient DB, or the inline placeholder."""
    global _map_cache
    if use_cache and _map_cache is not None:
        return _map_cache

    if path.exists():
        with path.open(encoding="utf-8") as fh:
            db = json.load(fh)
        cat_map = {
            name.lower(): entry["category"]
            for name, entry in db.items()
            if isinstance(entry, dict) and entry.get("category")
        }
    else:
        cat_map = dict(PLACEHOLDER_CATEGORY_MAP)

    if use_cache:
        _map_cache = cat_map
    return cat_map


def get_category(item_name: str, category_map: dict[str, str] | None = None) -> str:
    """Best-effort supermarket section for an ingredient / grocery item name."""
    name = item_name.strip().lower()
    cat_map = category_map if category_map is not None else _load_category_map()

    if name in cat_map:
        return cat_map[name]
    for keywords, category in _KEYWORD_RULES:
        if any(kw in name for kw in keywords):
            return category
    return "other"


def categorize_grocery_list(grocery_list: GroceryList) -> GroceryList:
    """Return a copy of the list with every item's ``category`` filled in."""
    cat_map = _load_category_map()
    new_items = [
        item.model_copy(update={"category": get_category(item.name, cat_map)})
        for item in grocery_list.items
    ]
    return grocery_list.model_copy(update={"items": new_items})


if __name__ == "__main__":  # smoke test
    assert get_category("chicken breast") in ("meat", "meat_poultry")  # DB file or placeholder map
    assert get_category("Chicken Thigh") == "meat"           # not in DB -> keyword rule, case-insensitive
    assert get_category("frozen peas") == "frozen"           # exact + would also keyword
    assert get_category("smoked salmon") == "seafood"        # keyword rule
    assert get_category("wholemeal bread") == "bakery"       # keyword rule
    assert get_category("orange juice") == "beverages"       # keyword rule
    assert get_category("plutonium") == "other"              # fallback
    for c in PLACEHOLDER_CATEGORY_MAP.values():
        assert c in KNOWN_CATEGORIES

    gl = GroceryList(
        items=[
            GroceryItem(name="chicken breast", quantity=600, unit="g", estimated_cost=7.0),
            GroceryItem(name="frozen peas", quantity=1, unit="pcs", estimated_cost=2.0),
            GroceryItem(name="mystery goo", quantity=1, unit="pcs", estimated_cost=1.0),
        ],
        estimated_total_cost=10.0,
        within_budget=True,
        shopping_day_index=0,
    )
    out = categorize_grocery_list(gl)
    print("categorized:", [(i.name, i.category) for i in out.items])
    cats = [i.category for i in out.items]
    # 'chicken breast' resolves via ingredients_db.json if present ('meat_poultry'),
    # otherwise via the placeholder map / keyword rule ('meat').
    assert cats[0] in ("meat", "meat_poultry")
    assert cats[1] == "frozen" and cats[2] == "other"
    print("supermarket_lookup smoke test OK")

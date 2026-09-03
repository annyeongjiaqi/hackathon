"""Local supermarket price lookup."""

from .nutrition_calculator import load_ingredient_db


def lookup_price(name: str, quantity_g: float, supermarket: str, database: dict | None = None) -> dict | None:
    record = (database or load_ingredient_db()).get(name.strip().lower())
    if not record:
        return None
    prices = record.get("prices_per_100g", {})
    price = prices.get(supermarket.lower(), prices.get("default", record.get("cost_per_100g")))
    if price is None:
        return None
    return {"name": name.strip().lower(), "quantity": quantity_g, "unit": "g", "estimated_cost": round(float(price) * quantity_g / 100, 2)}


def price_ingredients(ingredients: list[dict], supermarket: str, database: dict | None = None) -> tuple[list[dict], list[str]]:
    priced, missing = [], []
    for item in ingredients:
        value = lookup_price(item["name"], item["quantity"], supermarket, database)
        if value is None:
            missing.append(item["name"])
            priced.append({**item, "estimated_cost": None, "price_status": "estimate unavailable"})
        else:
            priced.append({**item, **value, "price_status": "estimated"})
    return priced, sorted(set(missing))

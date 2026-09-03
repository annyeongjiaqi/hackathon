"""Deterministic grocery-list validation."""

from .supermarket_lookup import price_ingredients


def validate_budget(items: list[dict], budget: float, supermarket: str, database: dict | None = None) -> dict:
    priced, missing = price_ingredients(items, supermarket, database)
    total = round(sum(i["estimated_cost"] or 0 for i in priced if not i.get("already_have")), 2)
    return {"valid": not missing and total <= budget, "total_cost": total, "items": priced, "missing_prices": missing, "over_by": round(max(0, total - budget), 2)}

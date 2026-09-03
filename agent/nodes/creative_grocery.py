"""Aggregate ingredients, subtract leftovers, price, and repair budget."""
from collections import defaultdict
from agent.tools.budget_validator import validate_budget
from agent.tools.substitution import suggest_substitution


def create_grocery(state: dict) -> dict:
    totals = defaultdict(float)
    for meal in state.get("meals", []):
        if meal.get("status") != "finished":
            for item in meal.get("ingredients", []): totals[item["name"].lower()] += float(item["quantity"])
    leftovers = state.get("leftover_ingredients", [])
    for leftover in leftovers:
        if isinstance(leftover, dict): totals[leftover["name"].lower()] = max(0, totals[leftover["name"].lower()] - float(leftover.get("quantity", 0)))
    items = [{"name": k, "quantity": v, "unit": "g", "already_have": v == 0} for k, v in totals.items() if v > 0]
    validation = validate_budget(items, float(state["budget"]), state["supermarket"])
    retries = 0
    while not validation["valid"] and not validation["missing_prices"] and retries < 3:
        expensive = max(validation["items"], key=lambda x: x["estimated_cost"])
        substitute = suggest_substitution(expensive["name"], state.get("dietary_restrictions"))
        if not substitute: break
        for item in items:
            if item["name"] == expensive["name"]: item["name"] = substitute
        validation = validate_budget(items, float(state["budget"]), state["supermarket"]); retries += 1
    return validation

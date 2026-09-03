"""Structured meal creation with an offline demo fallback."""
from __future__ import annotations
import json, os
from pathlib import Path
from langchain_aws import ChatBedrockConverse
from agent.constants import AWS_REGION, DEFAULT_MODEL_ID, MEAL_GENERATION_MAX_TOKENS, MEAL_GENERATION_TEMPERATURE
from agent.schemas import MealPlan
from agent.tools.nutrition_calculator import add_nutrition_to_meal
from agent.tools.shelf_life_rules import freshness_schedule

RECIPES = Path(__file__).parents[1] / "data" / "recipes_reference.json"


def _allowed(recipe: dict, state: dict) -> bool:
    appliances = set(a.lower() for a in state.get("appliances", []))
    restrictions = " ".join(state.get("dietary_restrictions", [])).lower()
    ingredients = " ".join(i["name"].lower() for i in recipe["ingredients"])
    needed = {"stove" if a.lower() == "stovetop" else a.lower() for a in recipe.get("appliances_used", recipe.get("appliances", []))}
    if not needed.issubset(appliances): return False
    if "vegetarian" in restrictions and any(x in ingredients for x in ("chicken", "salmon", "beef")): return False
    if "dairy-free" in restrictions and any(x in ingredients for x in ("yogurt", "milk", "cheese", "butter")): return False
    if "gluten-free" in restrictions and any(x in ingredients for x in ("wheat", "bread", "pasta")): return False
    return True


def _fallback(state: dict, days: list[int]) -> list[dict]:
    recipes = json.loads(RECIPES.read_text())
    constrained = [r for r in recipes if _allowed(r, state)]
    cuisines = [c.lower() for c in state.get("cuisine_preferences", [])]
    preferred = [r for r in constrained if any(c in r.get("cuisine", "").lower() or r.get("cuisine", "").lower() in c for c in cuisines)]
    # Cuisine is a ranking preference, not a hard filter: lead with matching
    # recipes while retaining variety from every otherwise valid option.
    allowed = preferred + [r for r in constrained if r not in preferred]
    if not allowed:
        raise ValueError("No offline recipe matches the selected appliances and dietary needs")
    daily_budget = float(state.get("budget", 0)) / max(1, len(days))
    within_budget = [r for r in allowed if float(r.get("cost_estimate_sgd", 0)) <= daily_budget]
    if within_budget:
        allowed = within_budget
    result = []
    for pos, day in enumerate(days):
        recipe = allowed[pos % len(allowed)]
        required = recipe.get("appliances_used", recipe.get("appliances", []))
        required = ["stove" if a.lower() == "stovetop" else a for a in required]
        meal = {**recipe, "day_index": day, "servings": 1 if state.get("living_alone_or_partner") == "alone" else 2, "steps": recipe.get("steps", ["Prepare ingredients.", "Cook until done and serve."]), "appliances_used": required, "estimated_prep_minutes": recipe.get("estimated_prep_minutes", 30), "status": "pending"}
        result.append(add_nutrition_to_meal(meal))
    return result


def create_meals(state: dict, days: list[int] | None = None) -> list[dict]:
    total = int(state["shopping_frequency_days"])
    days = days or list(range(1, total + 1))
    if os.getenv("USE_BEDROCK", "true").lower() != "true": return _fallback(state, days)
    model = ChatBedrockConverse(model=DEFAULT_MODEL_ID, region_name=AWS_REGION, temperature=MEAL_GENERATION_TEMPERATURE, max_tokens=MEAL_GENERATION_MAX_TOKENS).with_structured_output(MealPlan)
    tiers = freshness_schedule(total)
    prompt = f"Create exactly one dinner for each day {days}. Every ingredient quantity MUST use grams. User constraints: {state}. Freshness tiers by day: {tiers}. Use only appliances supplied, obey dietary restrictions and budget. Do not provide nutrition values."
    try:
        plan = model.invoke([("system", "You are a practical budget meal planner. Return the requested schema."), ("human", prompt)])
        return [add_nutrition_to_meal(m.model_dump()) for m in plan.meals]
    except Exception:
        if os.getenv("ALLOW_OFFLINE_FALLBACK", "true").lower() == "true": return _fallback(state, days)
        raise

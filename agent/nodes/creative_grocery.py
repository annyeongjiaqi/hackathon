"""Creative(grocery) — the LLM node that turns a validated meal plan into a
shopping list. Second of the roadmap's 4 triggered flows.

Deterministic work happens first (consolidate identical ingredients across meals,
flag anything already on hand from leftover tracking); the model's job is the
part that needs judgement: realistic purchase quantities / pack sizes and price
estimates for the user's supermarket. Output is forced into the ``GroceryList``
shape. Categories are assigned later by Decision-Support(grocery), not here.
"""

from __future__ import annotations

import json

from langchain_aws import ChatBedrockConverse

from agent.constants import (
    AWS_REGION,
    DEFAULT_MODEL_ID,
    GROCERY_GENERATION_MAX_TOKENS,
    GROCERY_GENERATION_TEMPERATURE,
)
from agent.schemas import GroceryList
from agent.state import MealPlanState
from agent.tools.nutrition_calculator import load_ingredients_db

SYSTEM_PROMPT = (
    "You are a grocery-shopping assistant. Given a consolidated list of ingredients "
    "needed for a meal plan, produce a realistic shopping list for one shop: round each "
    "quantity up to a sensible purchasable amount / pack size, and estimate the price of "
    "each line item in the local currency for the named supermarket. Keep the ingredient "
    "names exactly as given. Do not add ingredients that are not in the consolidated list. "
    "Any ingredient marked as already on hand must appear with already_have=true and "
    "estimated_cost=0, but still report the actual quantity the plan needs (never 0) so "
    "the amounts stay visible."
)


def consolidate_ingredients(meals: list[dict]) -> list[dict]:
    """Sum quantities for the same (name, unit) across every meal; one line each."""
    agg: dict[tuple[str, str], float] = {}
    for meal in meals:
        for ing in meal.get("ingredients", []):
            key = (ing["name"].strip().lower(), ing["unit"].strip().lower())
            agg[key] = agg.get(key, 0.0) + float(ing["quantity"])
    return [
        {"name": name, "unit": unit, "quantity": round(qty, 2)}
        for (name, unit), qty in sorted(agg.items())
    ]


def reference_costs(names: list[str]) -> dict[str, float]:
    """`cost_per_100g` from the ingredient DB for the given names, when known.

    Grounds the model's price estimates; missing names are simply omitted.
    """
    db = load_ingredients_db()
    out: dict[str, float] = {}
    for name in names:
        entry = db.get(name.strip().lower())
        if entry and entry.get("cost_per_100g") is not None:
            out[name] = float(entry["cost_per_100g"])
    return out


def build_messages(
    state: MealPlanState,
    consolidated: list[dict],
    already_have: set[str],
) -> list[tuple[str, str]]:
    budget = state.get("budget")
    supermarket = state.get("supermarket") or "a typical supermarket"
    shopping_day_index = int(state.get("days_until_next_shopping") or 0)

    marked = [
        {**row, "already_on_hand": row["name"] in already_have} for row in consolidated
    ]
    cost_hints = reference_costs([row["name"] for row in consolidated])

    lines = [
        f"Supermarket: {supermarket}.",
        f"Total grocery budget for this shop: {budget if budget is not None else 'not specified'}.",
        f"This shop covers shopping_day_index = {shopping_day_index}.",
        "",
        "Consolidated ingredients needed (already summed across all meals - do not split "
        "these back into per-meal lines):",
        json.dumps(marked, indent=2),
        "",
        f"Already on hand (set already_have=true, estimated_cost=0, but keep a real "
        f"non-zero quantity): {sorted(already_have) or 'none'}",
        "",
        "Reference costs (currency per 100 g / per 100 ml, for grounding your price "
        "estimates - scale to the quantity you decide to buy):",
        json.dumps(cost_hints, indent=2) if cost_hints else "  (none available)",
        "",
        "Rules:",
        "- One line item per ingredient; keep names exactly as given - never rename, drop, or "
        "substitute one for a different ingredient, even a cheaper one. This list must always "
        "match what the meal plan's recipes actually call for; a swap here without changing the "
        "recipe would make the shopping list wrong.",
        "- quantity = a realistic amount to actually buy (whole packs/units), >= the amount needed.",
        "- estimated_cost = your best price estimate for that quantity at this supermarket.",
        "- Fill estimated_total_cost and within_budget from your own numbers "
        "(Decision-Support will recompute deterministically).",
        f"- shopping_day_index = {shopping_day_index}.",
        "- Leave category as your best guess; it is normalised downstream.",
    ]

    feedback = (state.get("grocery_feedback") or "").strip()
    retry_count = int(state.get("grocery_retry_count") or 0)
    if feedback and retry_count > 0 and not feedback.lower().startswith("ok:"):
        lines += [
            "",
            "IMPORTANT - the previous shopping list failed validation. Read the problems "
            "below and fix them WITHOUT renaming, removing, or substituting any ingredient - "
            "every line item's name must stay exactly one of the consolidated ingredients "
            "above. If a problem mentions a cheaper alternative ingredient, that is context "
            "only, not permission to swap it in; bring the cost down by buying a smaller "
            "quantity / cheaper pack size of the SAME ingredient instead:",
            feedback,
        ]

    return [("system", SYSTEM_PROMPT), ("human", "\n".join(lines))]


def _build_model():
    return ChatBedrockConverse(
        model=DEFAULT_MODEL_ID,
        region_name=AWS_REGION,
        temperature=GROCERY_GENERATION_TEMPERATURE,
        max_tokens=GROCERY_GENERATION_MAX_TOKENS,
    )


def creative_grocery_node(state: MealPlanState) -> dict:
    """LangGraph node: build a candidate GroceryList, return a partial state update."""
    meals = state.get("meals") or []
    consolidated = consolidate_ingredients(meals)
    already_have = {x.strip().lower() for x in (state.get("leftover_ingredients") or [])}

    messages = build_messages(state, consolidated, already_have)
    model = _build_model().with_structured_output(GroceryList)
    grocery: GroceryList = model.invoke(messages)

    # Deterministically enforce the already-have flag from leftover tracking,
    # regardless of what the model did.
    items: list[dict] = []
    for item in grocery.items:
        if item.name.strip().lower() in already_have:
            item = item.model_copy(update={"already_have": True, "estimated_cost": 0.0})
        items.append(item.model_dump())

    attempt = int(state.get("grocery_retry_count") or 0) + 1
    return {
        "grocery_list": items,
        "grocery_retry_count": attempt,
        "log": [f"creative_grocery: attempt {attempt}, {len(items)} line item(s) "
                f"from {len(consolidated)} consolidated ingredient(s)"],
    }


if __name__ == "__main__":  # prompt-only smoke test (no Bedrock call)
    demo_meals = [
        {"ingredients": [{"name": "chicken breast", "quantity": 160, "unit": "g"},
                         {"name": "brown rice", "quantity": 80, "unit": "g"},
                         {"name": "olive oil", "quantity": 1, "unit": "tbsp"}]},
        {"ingredients": [{"name": "chicken breast", "quantity": 150, "unit": "g"},
                         {"name": "spinach", "quantity": 100, "unit": "g"},
                         {"name": "olive oil", "quantity": 1, "unit": "tbsp"}]},
    ]
    cons = consolidate_ingredients(demo_meals)
    print("consolidated:", cons)
    assert {"name": "chicken breast", "unit": "g", "quantity": 310.0} in cons
    assert {"name": "olive oil", "unit": "tbsp", "quantity": 2.0} in cons

    demo_state: MealPlanState = {
        "budget": 55.0, "supermarket": "FairPrice", "meals": demo_meals,
        "leftover_ingredients": ["olive oil"], "days_until_next_shopping": 0,
    }
    for role, content in build_messages(demo_state, cons, {"olive oil"}):
        print(f"----- {role} -----\n{content}\n")

"""Creative(meals) — the LLM node that generates the initial meal plan.

This is the first of the roadmap's 4 triggered flows. It reads the onboarding
fields off ``MealPlanState``, builds a grounded prompt (allowed pantry +
freshness constraints + a small recipe sample + any prior validation feedback),
and forces the model's answer into the ``MealPlan`` Pydantic shape via
``with_structured_output``.

No deterministic checks happen here — that's Decision-Support(meals). This node
only produces candidate meals and bumps the retry counter.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_aws import ChatBedrockConverse

from agent.constants import (
    AWS_REGION,
    DEFAULT_MODEL_ID,
    MEAL_GENERATION_MAX_TOKENS,
    MEAL_GENERATION_TEMPERATURE,
)
from agent.schemas import MealPlan
from agent.state import MealPlanState
from agent.tools.nutrition_calculator import load_ingredients_db
from agent.tools.shelf_life_rules import freshness_constraint_text

RECIPES_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "recipes_reference.json"

# --- Placeholder recipe reference -----------------------------------------------
# Used only until agent/data/recipes_reference.json exists with real content.
# Ingredient names are kept in sync with nutrition_calculator's placeholder DB so
# the generated plan can pass Decision-Support without a retry.
PLACEHOLDER_RECIPES_REFERENCE: list[dict] = [
    {"name": "Chicken, brown rice & broccoli bowl", "cuisine": "generic",
     "ingredients": ["chicken breast", "brown rice", "broccoli", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 25, "tags": ["high-protein"]},
    {"name": "Teriyaki-style salmon with rice & spinach", "cuisine": "japanese",
     "ingredients": ["salmon", "white rice", "spinach", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 20, "tags": ["high-protein", "omega-3"]},
    {"name": "Tofu & chickpea stir-fry", "cuisine": "asian",
     "ingredients": ["firm tofu", "canned chickpeas", "carrot", "onion", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 20, "tags": ["vegetarian", "vegan", "high-fibre"]},
    {"name": "Mediterranean lentil & spinach stew", "cuisine": "mediterranean",
     "ingredients": ["dried lentils", "spinach", "carrot", "onion", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 35, "tags": ["vegetarian", "vegan", "high-fibre"]},
    {"name": "Microwave oats breakfast bowl", "cuisine": "generic",
     "ingredients": ["rolled oats", "eggs"],
     "appliances": ["microwave"], "approx_prep_minutes": 6, "tags": ["quick", "vegetarian"]},
    {"name": "Egg fried rice with vegetables", "cuisine": "asian",
     "ingredients": ["white rice", "eggs", "carrot", "onion", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 15, "tags": ["quick", "vegetarian"]},
    {"name": "Chicken thigh traybake style (stovetop)", "cuisine": "mediterranean",
     "ingredients": ["chicken thigh", "carrot", "onion", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 30, "tags": ["high-protein"]},
    {"name": "Chickpea & spinach curry with rice", "cuisine": "indian",
     "ingredients": ["canned chickpeas", "spinach", "onion", "white rice", "olive oil"],
     "appliances": ["stovetop"], "approx_prep_minutes": 25, "tags": ["vegetarian", "vegan"]},
]

SYSTEM_PROMPT = (
    "You are a practical meal-planning assistant for a health-focused household. "
    "You design simple, realistic dinner plans that respect a fixed grocery budget, "
    "the household's kitchen equipment, dietary restrictions, and ingredient shelf life. "
    "You only use ingredients from the allowed pantry list you are given. "
    "You return exactly one meal per day for the requested number of days."
)


def load_recipes_reference(path: Path = RECIPES_REFERENCE_PATH) -> list[dict]:
    """Real recipe reference JSON if present and non-empty, else the placeholder."""
    if path.exists() and path.stat().st_size > 0:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if data:
            return data
    return PLACEHOLDER_RECIPES_REFERENCE


def _sample_recipes(
    recipes: list[dict],
    cuisine_preferences: list[str],
    dietary_restrictions: list[str],
    limit: int = 6,
) -> list[dict]:
    """A small, relevant slice of the reference to ground the prompt.

    Prefer recipes matching a cuisine preference; drop ones that obviously clash
    with a restriction keyword. Never send the whole file.
    """
    prefs = {c.strip().lower() for c in cuisine_preferences}
    restr = " ".join(dietary_restrictions).lower()

    def clashes(recipe: dict) -> bool:
        tags = {t.lower() for t in recipe.get("tags", [])}
        text = (recipe["name"] + " " + " ".join(recipe["ingredients"])).lower()
        if "vegetarian" in restr or "vegan" in restr:
            if tags & {"vegetarian", "vegan"}:
                return False
            return any(m in text for m in ("chicken", "salmon", "beef", "pork", "fish", "egg"))
        return False

    preferred = [r for r in recipes if r.get("cuisine", "").lower() in prefs and not clashes(r)]
    rest = [r for r in recipes if r not in preferred and not clashes(r)]
    return (preferred + rest)[:limit]


def _servings_for_household(living_alone_or_partner: str) -> int:
    return 1 if "alone" in (living_alone_or_partner or "").lower() else 2


def build_messages(state: MealPlanState) -> list[tuple[str, str]]:
    """Assemble the (system, human) message list for the model call."""
    total_plan_days = int(state.get("shopping_frequency_days") or 7)
    budget = state.get("budget")
    goal = state.get("goal") or "general healthy eating"
    dietary_restrictions = state.get("dietary_restrictions") or []
    appliances = state.get("appliances") or []
    cuisine_preferences = state.get("cuisine_preferences") or []
    household = state.get("living_alone_or_partner") or "partner"
    servings = _servings_for_household(household)

    allowed_pantry = sorted(load_ingredients_db().keys())
    recipe_sample = _sample_recipes(load_recipes_reference(), cuisine_preferences, dietary_restrictions)
    freshness = freshness_constraint_text(total_plan_days)

    lines = [
        f"Plan {total_plan_days} dinners, one per day, day_index 0 to {total_plan_days - 1}.",
        f"Household: {household} -> cook {servings} serving(s) per meal.",
        f"Weekly grocery budget for the whole plan: {budget if budget is not None else 'not specified'}.",
        f"Nutrition / health goal: {goal}.",
        f"Dietary restrictions (hard): {dietary_restrictions or 'none'}.",
        f"Available kitchen appliances (ONLY these): {appliances or 'basic stovetop'}.",
        f"Cuisine preferences: {cuisine_preferences or 'no strong preference'}.",
        "",
        "Shelf-life constraint:",
        f"  {freshness}",
        "",
        "Allowed pantry - every ingredient you use MUST be one of these exact names:",
        f"  {allowed_pantry}",
        "",
        "Reference recipes for inspiration (adapt freely, respect the constraints above):",
        json.dumps(recipe_sample, indent=2),
        "",
        "Rules:",
        "- Exactly one meal per day_index, no gaps, no duplicates of day_index.",
        "- appliances_used must be a subset of the available appliances.",
        "- Use only allowed-pantry ingredient names (lowercase, exact match).",
        "- Give realistic quantities with units ('g', 'ml', 'pcs', 'tbsp').",
        "- Leave nutrition null; it is computed downstream.",
        f"- servings = {servings} for every meal.",
    ]

    feedback = (state.get("meals_feedback") or "").strip()
    retry_count = int(state.get("meals_retry_count") or 0)
    if feedback and retry_count > 0 and not feedback.lower().startswith("all meals valid"):
        lines += [
            "",
            "IMPORTANT — your previous attempt failed validation. Fix exactly these problems "
            "and keep everything else the same where possible:",
            feedback,
        ]

    return [("system", SYSTEM_PROMPT), ("human", "\n".join(lines))]


def _build_model():
    return ChatBedrockConverse(
        model=DEFAULT_MODEL_ID,
        region_name=AWS_REGION,
        temperature=MEAL_GENERATION_TEMPERATURE,
        max_tokens=MEAL_GENERATION_MAX_TOKENS,
    )


def creative_meals_node(state: MealPlanState) -> dict:
    """LangGraph node: generate a candidate MealPlan, return a partial state update."""
    messages = build_messages(state)
    model = _build_model().with_structured_output(MealPlan)

    plan: MealPlan = model.invoke(messages)
    meals = [m.model_dump() for m in plan.meals]
    attempt = int(state.get("meals_retry_count") or 0) + 1

    return {
        "meals": meals,
        "meals_retry_count": attempt,
        "log": [f"creative_meals: attempt {attempt}, generated {len(meals)} meal(s)"],
    }


if __name__ == "__main__":  # prompt-only smoke test (no Bedrock call)
    demo_state: MealPlanState = {
        "budget": 55.0,
        "goal": "high protein, roughly 1800 kcal/day, more vegetables",
        "dietary_restrictions": ["no pork", "no shellfish"],
        "appliances": ["microwave", "stovetop"],
        "cuisine_preferences": ["mediterranean", "japanese"],
        "living_alone_or_partner": "alone",
        "shopping_frequency_days": 4,
    }
    for role, content in build_messages(demo_state):
        print(f"----- {role} -----\n{content}\n")

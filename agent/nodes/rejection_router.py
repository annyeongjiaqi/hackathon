"""Rejection router — plain-Python routing for the rejection-handling flow.

No LLM calls of its own. After Extraction(rejection reason) has classified the
complaint, this module:

  * preference_fixable  -> propose_substitution_node: swap the flagged ingredient
    using the shared substitution tool; if that swap is not usable, fall through
    to regeneration.
  * constraint_violated -> regenerate_rejected_meal_node: a scoped Creative(meals)
    call for just that one meal.

Both endings recompute the affected meal's nutrition deterministically (no LLM).
The DynamoDB write of the learned reason is a separate task, not done here.
"""

from __future__ import annotations

from agent.nodes.creative_meals import regenerate_single_meal
from agent.schemas import Meal
from agent.state import MealPlanState
from agent.tools.nutrition_calculator import calculate_meal_nutrition, load_ingredients_db
from agent.tools.substitution import suggest_substitute

# A curated substitute below this confidence, or one we cannot cost/score
# nutritionally (not in the ingredient DB), is treated as "not good enough" and
# we regenerate the whole meal instead.
SUBSTITUTION_CONFIDENCE_MIN = 0.5


# --------------------------------------------------------------------------- #
# Routing predicates
# --------------------------------------------------------------------------- #

def route_after_extraction(state: MealPlanState) -> str:
    """preference_fixable -> substitution; anything else -> regeneration."""
    return "substitution" if state.get("rejection_category") == "preference_fixable" else "regeneration"


def route_after_substitution(state: MealPlanState) -> str:
    """Substitution applied cleanly -> done; otherwise fall back to regeneration."""
    return "done" if state.get("rejection_outcome") == "substituted" else "regeneration"


# --------------------------------------------------------------------------- #
# Action nodes
# --------------------------------------------------------------------------- #

def _recompute_nutrition(meal: dict, db: dict) -> tuple[dict, list[str]]:
    """Return (meal-with-nutrition, skipped_ingredients)."""
    result = calculate_meal_nutrition(Meal(**meal).ingredients, db)
    meal = {**meal, "nutrition": result.nutrition.model_dump()}
    return meal, result.skipped_ingredients


def propose_substitution_node(state: MealPlanState) -> dict:
    """Try a single-ingredient swap on the rejected meal (preference branch)."""
    meals = list(state.get("meals") or [])
    idx = int(state.get("rejected_meal_index") or 0)
    if not (0 <= idx < len(meals)):
        return {"rejection_outcome": "needs_regeneration",
                "log": [f"propose_substitution: bad rejected_meal_index {idx}"]}

    meal = meals[idx]
    reason = (state.get("rejection_reason_summary") or state.get("rejection_reason_raw") or "").strip()
    target = (state.get("rejection_target_ingredient") or "").strip().lower()

    ingredient_names = {i["name"].strip().lower() for i in meal.get("ingredients", [])}
    if not target or target not in ingredient_names:
        return {
            "rejection_outcome": "needs_regeneration",
            "log": [f"propose_substitution: no swappable ingredient identified "
                    f"(target={target or 'none'}); handing to regeneration"],
        }

    suggestion = suggest_substitute(target, reason or "user dislikes this ingredient")
    db = load_ingredients_db()
    sub = (suggestion.substitute or "").strip().lower()
    usable = (
        suggestion.found
        and sub
        and suggestion.confidence >= SUBSTITUTION_CONFIDENCE_MIN
        and sub in db
    )
    if not usable:
        return {
            "rejection_outcome": "needs_regeneration",
            "log": [f"propose_substitution: '{target}' -> "
                    f"{suggestion.substitute or 'no candidate'} "
                    f"(found={suggestion.found}, conf={suggestion.confidence}, "
                    f"in_db={sub in db if sub else False}) not usable; regenerating meal"],
        }

    new_ingredients = [
        {**i, "name": sub} if i["name"].strip().lower() == target else i
        for i in meal["ingredients"]
    ]
    fixed = {**meal, "ingredients": new_ingredients, "status": "pending"}
    fixed, skipped = _recompute_nutrition(fixed, db)
    meals[idx] = fixed

    log = [f"propose_substitution: swapped '{target}' -> '{sub}' "
           f"(conf {suggestion.confidence}); nutrition recomputed"]
    if skipped:
        log.append(f"propose_substitution: WARNING nutrition skipped {skipped}")
    return {"meals": meals, "rejection_outcome": "substituted", "log": log}


def regenerate_rejected_meal_node(state: MealPlanState) -> dict:
    """Scoped Creative(meals) call for the one rejected meal (constraint branch,
    or substitution fallback)."""
    meals = list(state.get("meals") or [])
    idx = int(state.get("rejected_meal_index") or 0)
    if not (0 <= idx < len(meals)):
        return {"log": [f"regenerate_rejected_meal: bad rejected_meal_index {idx}"]}

    guidance = (
        state.get("rejection_reason_summary")
        or state.get("rejection_reason_raw")
        or "the user rejected this meal; produce a clearly different one"
    ).strip()

    new_meal = regenerate_single_meal(state, idx, guidance)
    db = load_ingredients_db()
    new_meal, skipped = _recompute_nutrition(new_meal, db)
    meals[idx] = new_meal

    log = [f"regenerate_rejected_meal: replaced meal {idx} (day "
           f"{new_meal.get('day_index')}) -> '{new_meal.get('name')}'"]
    if skipped:
        log.append(f"regenerate_rejected_meal: WARNING nutrition skipped {skipped}")
    return {"meals": meals, "rejection_outcome": "regenerated", "log": log}


if __name__ == "__main__":  # offline smoke test for the deterministic parts
    base_meal = {
        "name": "Garlic Chicken with Broccoli", "day_index": 0, "servings": 1,
        "ingredients": [
            {"name": "chicken breast", "quantity": 150, "unit": "g"},
            {"name": "broccoli", "quantity": 200, "unit": "g"},
            {"name": "garlic", "quantity": 6, "unit": "pcs"},
        ],
        "appliances_used": ["stovetop"], "estimated_prep_minutes": 30, "status": "rejected",
    }
    state: MealPlanState = {
        "meals": [base_meal], "appliances": ["stovetop"], "rejected_meal_index": 0,
    }

    # routing
    assert route_after_extraction({"rejection_category": "preference_fixable"}) == "substitution"
    assert route_after_extraction({"rejection_category": "constraint_violated"}) == "regeneration"
    assert route_after_substitution({"rejection_outcome": "substituted"}) == "done"
    assert route_after_substitution({"rejection_outcome": "needs_regeneration"}) == "regeneration"

    # substitution with no identified ingredient -> hands off to regeneration
    out = propose_substitution_node({**state, "rejection_reason_summary": "too bland overall"})
    assert out["rejection_outcome"] == "needs_regeneration", out
    print("no-target ->", out["log"][0])

    # substitution with a target that has no usable curated swap in our pantry
    out2 = propose_substitution_node({
        **state,
        "rejection_target_ingredient": "garlic",
        "rejection_reason_summary": "way too much garlic",
    })
    print("garlic target ->", out2["rejection_outcome"], "|", out2["log"][0])
    assert out2["rejection_outcome"] in ("substituted", "needs_regeneration")

    print("rejection_router smoke test OK")

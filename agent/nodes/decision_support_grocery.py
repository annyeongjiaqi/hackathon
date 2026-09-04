"""Decision-Support(grocery) — deterministic validation of a candidate grocery list.

Plain Python, no LLM. Runs right after Creative(grocery):
  1. assign each item's supermarket category
     (reuses supermarket_lookup.categorize_grocery_list)
  2. recompute the total and check it against the budget
     (reuses budget_validator.validate_grocery_budget)
  3. if over budget, attach concrete cheaper-swap ideas to the feedback
     (reuses substitution.suggest_substitute) so the retry can act on them -
     only if the substitute is an actual pantry ingredient (see BUG below)
  4. every grocery item name must be one of the meal plan's own consolidated
     ingredients, and every consolidated ingredient must be in the grocery
     list - no more, no less (see BUG below)

Retry gating mirrors the meal flow: ``GroceryValidationResult.should_retry`` /
GROCERY_VALIDATION_MAX_RETRIES.

--------------------------------------------------------------------------
BUG found in live testing (round 2 of the pre-demo test pass) and fixed here:
a 7-day, tight-budget run came back with a grocery list asking the user to
buy "chicken thigh" - an ingredient that does not exist in
ingredients_db.json and that none of the plan's actual meals used (they all
called for "chicken breast"). Root cause, two compounding issues:
  * this module suggested the swap unconditionally - ``suggest_substitute()``
    returned "chicken thigh" (a real candidate in substitution.py's curated
    table) without checking it was an ingredient this app actually stocks.
  * creative_grocery.py's retry prompt told the model to "swap costly items
    for the cheaper alternatives suggested", so it renamed the line item -
    but Creative(grocery) never touches ``meals``, so the recipe still called
    for chicken breast while the shopping list said chicken thigh.
Fixed on both sides: the swap suggestion here is now dropped unless the
substitute is a real pantry ingredient, AND (defense in depth, since a
prompt-level instruction is not a guarantee) ``validate_grocery`` now takes
the meal plan's own consolidated ingredient names and hard-fails any grocery
list that doesn't match them exactly - the same pattern as the pantry/
appliance/exclusion checks in decision_support_meals.py. creative_grocery.py's
prompt no longer invites a rename at all; see its rule text.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.constants import GROCERY_VALIDATION_MAX_RETRIES
from agent.nodes.creative_grocery import consolidate_ingredients
from agent.schemas import GroceryItem, GroceryList
from agent.state import MealPlanState
from agent.tools.budget_validator import validate_grocery_budget
from agent.tools.nutrition_calculator import load_ingredients_db
from agent.tools.substitution import suggest_substitute
from agent.tools.supermarket_lookup import categorize_grocery_list


@dataclass
class GroceryValidationResult:
    valid: bool
    feedback: str
    grocery_list: list[dict]      # categorized, corrected costs
    total_cost: float
    budget: float
    swaps: list[str] = field(default_factory=list)

    def should_retry(self, retry_count: int) -> bool:
        return (not self.valid) and retry_count < GROCERY_VALIDATION_MAX_RETRIES


def validate_grocery(
    grocery_items: list[dict],
    budget: float,
    *,
    shopping_day_index: int = 0,
    expected_ingredient_names: set[str] | None = None,
) -> GroceryValidationResult:
    """``expected_ingredient_names`` should be the meal plan's own consolidated
    ingredient names (``{row["name"] for row in consolidate_ingredients(meals)}``)
    - when given, the grocery list must name exactly those ingredients, no more
    and no less. Catches Creative(grocery) renaming or substituting a line item
    (it must only price/package what the meals actually need - see module BUG
    note) even when nothing is over budget."""
    gl = GroceryList(
        items=[GroceryItem(**d) for d in grocery_items],
        estimated_total_cost=0.0,
        within_budget=False,
        shopping_day_index=shopping_day_index,
    )
    gl = categorize_grocery_list(gl)
    budget_result = validate_grocery_budget(gl, budget)
    db = load_ingredients_db()

    feedback_parts = [budget_result.feedback]
    swaps: list[str] = []
    if not budget_result.valid:
        for name, cost in budget_result.priciest_items:
            suggestion = suggest_substitute(
                name, "grocery bill is over budget, need a cheaper alternative"
            )
            # Only surface a swap idea if it's an ingredient this app actually
            # stocks - suggest_substitute()'s curated table predates the real
            # ingredients_db and can name something (e.g. "chicken thigh")
            # that isn't in it, which would be ungrounded advice.
            if suggestion.substitute and suggestion.substitute.strip().lower() in db:
                swaps.append(
                    f"- replace {name} (~${cost:.2f}) with {suggestion.substitute}: "
                    f"{suggestion.rationale} (for reference only - do not rename the line "
                    f"item; see the ingredient-mismatch rule)"
                )
        if swaps:
            feedback_parts.append("Cheaper-swap ideas for context (NOT for renaming):\n" + "\n".join(swaps))

    mismatch_valid = True
    if expected_ingredient_names is not None:
        expected = {n.strip().lower() for n in expected_ingredient_names}
        got = {i.name.strip().lower() for i in gl.items}
        extra = sorted(got - expected)
        missing = sorted(expected - got)
        if extra:
            mismatch_valid = False
            feedback_parts.append(
                f"Ingredient mismatch: the list includes {extra}, which no meal in this plan "
                f"uses. Creative(grocery) must only price/package the meal plan's own "
                f"ingredients - remove these and restore the ingredient(s) actually needed, "
                f"under their original name."
            )
        if missing:
            mismatch_valid = False
            feedback_parts.append(
                f"Ingredient mismatch: the list is missing {missing}, which the meal plan "
                f"needs. Add them back under their original name."
            )

    valid = budget_result.valid and mismatch_valid
    feedback = "\n".join(feedback_parts)

    return GroceryValidationResult(
        valid=valid,
        feedback=feedback,
        grocery_list=[i.model_dump() for i in budget_result.grocery_list.items],
        total_cost=budget_result.total_cost,
        budget=budget_result.budget,
        swaps=swaps,
    )


def decision_support_grocery_node(state: MealPlanState) -> dict:
    """LangGraph node: validate state['grocery_list'], return a partial state update.

    Tracks the cheapest attempt seen across retries (``grocery_best_attempt``).
    On retry-exhaustion without ever landing under budget, returns that
    cheapest attempt instead of whichever one happened to run last - retry/
    feedback logic itself (routing, retry count, should_retry) is unchanged.
    """
    budget = float(state.get("budget") or 0.0)
    consolidated = consolidate_ingredients(state.get("meals") or [])
    result = validate_grocery(
        state.get("grocery_list") or [],
        budget,
        shopping_day_index=int(state.get("days_until_next_shopping") or 0),
        expected_ingredient_names={row["name"] for row in consolidated},
    )
    retry_count = int(state.get("grocery_retry_count") or 0)

    current_attempt = {
        "grocery_list": result.grocery_list,
        "total_cost": result.total_cost,
        "feedback": result.feedback,
    }
    best = state.get("grocery_best_attempt")
    if best is None or result.total_cost < best["total_cost"]:
        best = current_attempt

    if result.valid or result.should_retry(retry_count):
        status = "valid" if result.valid else "invalid"
        return {
            "grocery_list": result.grocery_list,
            "grocery_valid": result.valid,
            "grocery_feedback": result.feedback,
            "grocery_best_attempt": best,
            "log": [f"decision_support_grocery: {status} "
                    f"(${result.total_cost:.2f} / ${budget:.2f}) after attempt {retry_count}"],
        }

    # Retries exhausted, never landed under budget: return the cheapest attempt
    # seen across the whole run, not just this last one.
    return {
        "grocery_list": best["grocery_list"],
        "grocery_valid": False,
        "grocery_feedback": best["feedback"],
        "grocery_best_attempt": best,
        "log": [f"decision_support_grocery: retries exhausted, returning cheapest attempt seen "
                f"(${best['total_cost']:.2f} / ${budget:.2f}) after attempt {retry_count}"],
    }


if __name__ == "__main__":  # smoke test (no Bedrock call)
    within = [
        {"name": "chicken breast", "quantity": 500, "unit": "g", "estimated_cost": 6.5},
        {"name": "brown rice", "quantity": 1, "unit": "kg", "estimated_cost": 3.0},
        {"name": "spinach", "quantity": 200, "unit": "g", "estimated_cost": 2.5},
        {"name": "olive oil", "quantity": 1, "unit": "pcs", "estimated_cost": 0.0, "already_have": True},
    ]
    r1 = validate_grocery(within, budget=20.0)
    print(r1.feedback)
    assert r1.valid and r1.total_cost == 12.0
    cats = {i["name"]: i["category"] for i in r1.grocery_list}
    # categories come from ingredients_db.json when present, else keyword fallback
    assert cats["chicken breast"] in ("meat", "meat_poultry")
    assert "veget" in cats["spinach"] or cats["spinach"] == "produce"
    assert r1.should_retry(0) is False

    over = within + [{"name": "ribeye steak", "quantity": 2, "unit": "pcs", "estimated_cost": 26.0}]
    r2 = validate_grocery(over, budget=20.0)
    print(r2.feedback)
    assert not r2.valid
    # substitution.py's curated top pick for "ribeye steak" is "chicken thigh" - not a real
    # pantry ingredient (see module BUG note) - the guard must drop it, not suggest it
    assert not any("chicken thigh" in s for s in r2.swaps), r2.swaps
    assert "chicken thigh" not in r2.feedback
    assert r2.should_retry(0) is True
    assert r2.should_retry(GROCERY_VALIDATION_MAX_RETRIES) is False

    # ingredient-mismatch backstop: Creative(grocery) renamed "chicken breast" to
    # "chicken thigh" (this is the actual bug reproduced end-to-end, live, in round 2
    # of the pre-demo test pass) - must fail even though nothing is over budget
    renamed = [
        {"name": "chicken thigh", "quantity": 500, "unit": "g", "estimated_cost": 4.0},
        {"name": "brown rice", "quantity": 1, "unit": "kg", "estimated_cost": 3.0},
    ]
    r3 = validate_grocery(
        renamed, budget=20.0,
        expected_ingredient_names={"chicken breast", "brown rice"},
    )
    print(r3.feedback)
    assert not r3.valid, "a renamed ingredient must fail validation even within budget"
    assert "chicken thigh" in r3.feedback and "no meal in this plan uses" in r3.feedback
    assert "chicken breast" in r3.feedback and "missing" in r3.feedback.lower()
    assert r3.should_retry(0) is True

    # matching ingredients + no expected set given -> unaffected (existing behavior)
    r4 = validate_grocery(within, budget=20.0, expected_ingredient_names=None)
    assert r4.valid == r1.valid and r4.feedback == r1.feedback

    print("decision_support_grocery smoke test OK")

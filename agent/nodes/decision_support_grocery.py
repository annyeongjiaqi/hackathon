"""Decision-Support(grocery) — deterministic validation of a candidate grocery list.

Plain Python, no LLM. Runs right after Creative(grocery):
  1. assign each item's supermarket category
     (reuses supermarket_lookup.categorize_grocery_list)
  2. recompute the total and check it against the budget
     (reuses budget_validator.validate_grocery_budget)
  3. if over budget, attach concrete cheaper-swap ideas to the feedback
     (reuses substitution.suggest_substitute) so the retry can act on them

Retry gating mirrors the meal flow: ``GroceryValidationResult.should_retry`` /
GROCERY_VALIDATION_MAX_RETRIES.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.constants import GROCERY_VALIDATION_MAX_RETRIES
from agent.schemas import GroceryItem, GroceryList
from agent.state import MealPlanState
from agent.tools.budget_validator import validate_grocery_budget
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
) -> GroceryValidationResult:
    gl = GroceryList(
        items=[GroceryItem(**d) for d in grocery_items],
        estimated_total_cost=0.0,
        within_budget=False,
        shopping_day_index=shopping_day_index,
    )
    gl = categorize_grocery_list(gl)
    budget_result = validate_grocery_budget(gl, budget)

    feedback = budget_result.feedback
    swaps: list[str] = []
    if not budget_result.valid:
        for name, cost in budget_result.priciest_items:
            suggestion = suggest_substitute(
                name, "grocery bill is over budget, need a cheaper alternative"
            )
            if suggestion.substitute:
                swaps.append(
                    f"- replace {name} (~${cost:.2f}) with {suggestion.substitute}: "
                    f"{suggestion.rationale}"
                )
        if swaps:
            feedback = feedback + "\nCheaper-swap ideas for the retry:\n" + "\n".join(swaps)

    return GroceryValidationResult(
        valid=budget_result.valid,
        feedback=feedback,
        grocery_list=[i.model_dump() for i in budget_result.grocery_list.items],
        total_cost=budget_result.total_cost,
        budget=budget_result.budget,
        swaps=swaps,
    )


def decision_support_grocery_node(state: MealPlanState) -> dict:
    """LangGraph node: validate state['grocery_list'], return a partial state update."""
    budget = float(state.get("budget") or 0.0)
    result = validate_grocery(
        state.get("grocery_list") or [],
        budget,
        shopping_day_index=int(state.get("days_until_next_shopping") or 0),
    )
    retry_count = int(state.get("grocery_retry_count") or 0)
    status = "valid" if result.valid else "over budget"
    return {
        "grocery_list": result.grocery_list,
        "grocery_valid": result.valid,
        "grocery_feedback": result.feedback,
        "log": [f"decision_support_grocery: {status} "
                f"(${result.total_cost:.2f} / ${budget:.2f}) after attempt {retry_count}"],
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
    assert any("ribeye steak" in s and "chicken thigh" in s for s in r2.swaps)
    assert r2.should_retry(0) is True
    assert r2.should_retry(GROCERY_VALIDATION_MAX_RETRIES) is False
    print("decision_support_grocery smoke test OK")

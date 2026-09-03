"""Budget validator for the grocery list (deterministic, no LLM).

Implements "Decision-Support (grocery)": recompute the grocery list's total from
its line items, compare against the user's budget, and return a pass/fail signal
plus a feedback string. The graph uses ``result.valid`` as the retry-loop
condition and feeds ``result.feedback`` back into Creative(grocery) (which then
calls the shared substitution tool to swap costly items for cheaper ones).

Retry bounding uses ``GROCERY_VALIDATION_MAX_RETRIES`` from ``agent.constants``
(see roadmap Key Risk #3 — every loop must be bounded).

Usage:
    >>> from agent.schemas import GroceryList, GroceryItem
    >>> gl = GroceryList(items=[GroceryItem(name="steak", quantity=2, unit="pcs",
    ...                                     estimated_cost=30.0)],
    ...                  estimated_total_cost=0, within_budget=True, shopping_day_index=0)
    >>> res = validate_grocery_budget(gl, budget=20.0)
    >>> res.valid, round(res.overage, 2)
    (False, 10.0)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.constants import GROCERY_VALIDATION_MAX_RETRIES
from agent.schemas import GroceryList

# Fraction over budget we tolerate before failing (rounding / estimate slack).
BUDGET_TOLERANCE = 0.02


@dataclass
class BudgetValidationResult:
    valid: bool
    feedback: str
    total_cost: float
    budget: float
    overage: float                      # max(0, total - budget)
    grocery_list: GroceryList           # copy with corrected totals / within_budget
    priciest_items: list[tuple[str, float]] = field(default_factory=list)

    def should_retry(self, retry_count: int) -> bool:
        """True if the graph should loop Creative(grocery) again."""
        return (not self.valid) and retry_count < GROCERY_VALIDATION_MAX_RETRIES


def compute_total_cost(grocery_list: GroceryList) -> float:
    """Sum ``estimated_cost`` over items that actually need to be bought."""
    return round(
        sum(item.estimated_cost for item in grocery_list.items if not item.already_have),
        2,
    )


def validate_grocery_budget(grocery_list: GroceryList, budget: float) -> BudgetValidationResult:
    """Recompute the total, check it against ``budget``, return a loop signal."""
    total = compute_total_cost(grocery_list)
    overage = round(max(0.0, total - budget), 2)
    within = total <= budget * (1 + BUDGET_TOLERANCE)

    corrected = grocery_list.model_copy(
        update={"estimated_total_cost": total, "within_budget": within}
    )

    priciest = sorted(
        ((i.name, i.estimated_cost) for i in grocery_list.items if not i.already_have),
        key=lambda kv: kv[1],
        reverse=True,
    )[:3]

    if within:
        feedback = f"OK: ${total:.2f} of ${budget:.2f} budget ({budget - total:.2f} to spare)."
    else:
        offenders = ", ".join(f"{n} (${c:.2f})" for n, c in priciest)
        feedback = (
            f"Over budget by ${overage:.2f} (${total:.2f} vs ${budget:.2f}). "
            f"Substitute or reduce the priciest items: {offenders}."
        )

    return BudgetValidationResult(
        valid=within,
        feedback=feedback,
        total_cost=total,
        budget=budget,
        overage=overage,
        grocery_list=corrected,
        priciest_items=priciest,
    )


if __name__ == "__main__":  # smoke test
    from agent.schemas import GroceryItem

    under = GroceryList(
        items=[
            GroceryItem(name="chicken breast", quantity=600, unit="g", estimated_cost=7.5),
            GroceryItem(name="brown rice", quantity=1, unit="kg", estimated_cost=3.0),
            GroceryItem(name="broccoli", quantity=2, unit="head", estimated_cost=2.5),
            GroceryItem(name="olive oil", quantity=1, unit="pcs", estimated_cost=0.0, already_have=True),
        ],
        estimated_total_cost=999,  # deliberately wrong -> should be recomputed
        within_budget=False,
        shopping_day_index=0,
    )
    r1 = validate_grocery_budget(under, budget=20.0)
    print(r1.feedback)
    assert r1.valid and r1.total_cost == 13.0 and r1.overage == 0.0
    assert r1.grocery_list.estimated_total_cost == 13.0 and r1.grocery_list.within_budget

    over = under.model_copy(update={
        "items": under.items + [GroceryItem(name="ribeye steak", quantity=2, unit="pcs", estimated_cost=24.0)]
    })
    r2 = validate_grocery_budget(over, budget=20.0)
    print(r2.feedback)
    assert not r2.valid and r2.overage == 17.0
    assert r2.priciest_items[0] == ("ribeye steak", 24.0)
    assert r2.should_retry(retry_count=0) is True
    assert r2.should_retry(retry_count=GROCERY_VALIDATION_MAX_RETRIES) is False

    # tolerance: $20.30 on a $20 budget still passes (< 2% over)
    r3 = validate_grocery_budget(
        GroceryList(items=[GroceryItem(name="x", quantity=1, unit="pcs", estimated_cost=20.30)],
                    estimated_total_cost=0, within_budget=False, shopping_day_index=0),
        budget=20.0,
    )
    assert r3.valid, r3.feedback
    print("budget_validator smoke test OK")

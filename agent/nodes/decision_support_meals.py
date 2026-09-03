"""Decision-Support(meals) — deterministic validation of a candidate MealPlan.

Plain Python, no LLM. Runs right after Creative(meals):
  1. every ingredient must exist in the ingredients DB
     (reuses nutrition_calculator.load_ingredients_db — not reimplemented)
  2. fill each meal's nutrition via nutrition_calculator.fill_meal_nutrition
  3. every meal's appliances_used must be a subset of the household's appliances
  4. one meal per expected day_index (no gaps / duplicates)

Returns a pass/fail + a feedback string. Retry gating mirrors budget_validator:
``MealValidationResult.should_retry(retry_count)`` against MEAL_VALIDATION_MAX_RETRIES.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.constants import MEAL_VALIDATION_MAX_RETRIES
from agent.schemas import Meal
from agent.state import MealPlanState
from agent.tools.nutrition_calculator import fill_meal_nutrition, load_ingredients_db


@dataclass
class MealValidationResult:
    valid: bool
    feedback: str
    meals: list[dict]                       # revalidated, nutrition filled
    problems: list[str] = field(default_factory=list)

    def should_retry(self, retry_count: int) -> bool:
        return (not self.valid) and retry_count < MEAL_VALIDATION_MAX_RETRIES


def validate_meals(
    meals: list[dict],
    appliances: list[str],
    *,
    expected_days: int | None = None,
    ingredients_db: dict[str, dict] | None = None,
) -> MealValidationResult:
    db = ingredients_db if ingredients_db is not None else load_ingredients_db()
    available = {a.strip().lower() for a in (appliances or [])}
    problems: list[str] = []
    filled: list[dict] = []
    seen_days: list[int] = []

    if not meals:
        return MealValidationResult(False, "No meals were generated.", [], ["empty meal list"])

    for raw in meals:
        meal = Meal(**raw)  # re-run pydantic validation on the dict form
        tag = f"'{meal.name}' (day {meal.day_index})"
        seen_days.append(meal.day_index)

        missing = sorted(
            {ing.name for ing in meal.ingredients if ing.name.strip().lower() not in db}
        )
        if missing:
            problems.append(f"{tag}: ingredients not in the pantry DB: {missing}")

        extra = sorted({a for a in meal.appliances_used if a.strip().lower() not in available})
        if extra and available:
            problems.append(
                f"{tag}: uses appliances the household does not have: {extra} "
                f"(available: {sorted(available)})"
            )

        filled.append(fill_meal_nutrition(meal, db).model_dump())

    # day coverage
    if expected_days is not None:
        want = set(range(expected_days))
        got = set(seen_days)
        if got != want:
            missing_days = sorted(want - got)
            extra_days = sorted(got - want)
            msg = []
            if missing_days:
                msg.append(f"missing day_index {missing_days}")
            if extra_days:
                msg.append(f"unexpected day_index {extra_days}")
            problems.append("day coverage wrong: " + ", ".join(msg))
    if len(seen_days) != len(set(seen_days)):
        dupes = sorted({d for d in seen_days if seen_days.count(d) > 1})
        problems.append(f"duplicate meals for day_index {dupes}")

    valid = not problems
    feedback = (
        "All meals valid."
        if valid
        else "Fix these problems and regenerate only the affected meals:\n- "
        + "\n- ".join(problems)
    )
    return MealValidationResult(valid=valid, feedback=feedback, meals=filled, problems=problems)


def decision_support_meals_node(state: MealPlanState) -> dict:
    """LangGraph node: validate state['meals'], return a partial state update."""
    expected_days = state.get("shopping_frequency_days")
    result = validate_meals(
        state.get("meals") or [],
        state.get("appliances") or [],
        expected_days=int(expected_days) if expected_days else None,
    )
    retry_count = int(state.get("meals_retry_count") or 0)
    status = "valid" if result.valid else f"invalid ({len(result.problems)} problem(s))"
    return {
        "meals": result.meals,
        "meals_valid": result.valid,
        "meals_feedback": result.feedback,
        "log": [f"decision_support_meals: {status} after attempt {retry_count}"],
    }


if __name__ == "__main__":  # smoke test (no Bedrock call)
    good = [
        {"name": "Chicken rice bowl", "day_index": 0, "servings": 1,
         "ingredients": [{"name": "chicken breast", "quantity": 150, "unit": "g"},
                         {"name": "brown rice", "quantity": 75, "unit": "g"},
                         {"name": "broccoli", "quantity": 120, "unit": "g"}],
         "appliances_used": ["stovetop"], "estimated_prep_minutes": 25},
        {"name": "Tofu stir-fry", "day_index": 1, "servings": 1,
         "ingredients": [{"name": "firm tofu", "quantity": 150, "unit": "g"},
                         {"name": "carrot", "quantity": 1, "unit": "pcs"}],
         "appliances_used": ["stovetop"], "estimated_prep_minutes": 20},
    ]
    r = validate_meals(good, ["microwave", "stovetop"], expected_days=2)
    print(r.feedback)
    assert r.valid and r.meals[0]["nutrition"]["calories"] > 0
    assert r.should_retry(0) is False

    bad = [
        {"name": "Air-fried mystery", "day_index": 0, "servings": 1,
         "ingredients": [{"name": "unobtainium", "quantity": 100, "unit": "g"}],
         "appliances_used": ["air fryer"], "estimated_prep_minutes": 15},
    ]
    r2 = validate_meals(bad, ["stovetop"], expected_days=2)
    print(r2.feedback)
    assert not r2.valid
    assert any("pantry DB" in p for p in r2.problems)
    assert any("does not have" in p for p in r2.problems)
    assert any("day coverage" in p for p in r2.problems)
    assert r2.should_retry(0) is True
    assert r2.should_retry(MEAL_VALIDATION_MAX_RETRIES) is False
    print("decision_support_meals smoke test OK")

"""Decision-Support(meals) — deterministic validation of a candidate MealPlan.

Plain Python, no LLM. Runs right after Creative(meals):
  1. every ingredient must exist in the ingredients DB
     (reuses nutrition_calculator.load_ingredients_db — not reimplemented)
  2. compute each meal's nutrition via nutrition_calculator.calculate_meal_nutrition;
     any ``skipped_ingredients`` it reports (not in DB / unit not convertible)
     makes that meal INVALID, with a precise, actionable correction in the feedback
  3. every meal's appliances_used must be a subset of the household's appliances
  4. one meal per expected day_index (no gaps / duplicates)
  5. no ingredient from this session's excluded_ingredients_for_session()
     (learned from past preference_fixable rejections) - this is the backstop
     for creative_meals.py's prompt-level "HARD EXCLUSION" instruction: the
     model is TOLD not to use e.g. garlic, but nothing before this enforced it
     code-side if it slipped through anyway

Returns a pass/fail + a feedback string. Retry gating mirrors budget_validator:
``MealValidationResult.should_retry(retry_count)`` against MEAL_VALIDATION_MAX_RETRIES.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.constants import MEAL_VALIDATION_MAX_RETRIES
from agent.nodes.personalized import excluded_ingredients_for_session
from agent.schemas import Meal
from agent.state import MealPlanState
from agent.tools.nutrition_calculator import calculate_meal_nutrition, load_ingredients_db

# nutrition_calculator emits skip reasons as "<name> (<detail>)".
_SKIP_REASON_RE = re.compile(r"^(?P<name>.+?) \((?P<detail>.+)\)$")


def _actionable_skip(reason: str) -> str:
    """Turn a raw skip reason into a correction Creative(meals) can act on."""
    match = _SKIP_REASON_RE.match(reason)
    if not match:
        return reason
    name, detail = match.group("name"), match.group("detail")
    if "not convertible" in detail:
        return (
            f"{name}: {detail} - use 'g'/'kg' for a solid, or 'ml'/'tbsp'/'tsp'/'cup' "
            f"for a liquid or condiment"
        )
    if "not in ingredients DB" in detail:
        return f"{name}: not in the ingredient database - choose a replacement from the pantry list"
    return f"{name}: {detail}"


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
    excluded_ingredients: dict[str, str] | None = None,
) -> MealValidationResult:
    """``excluded_ingredients`` should be the SAME dict (ingredient -> reason)
    ``creative_meals.py`` used to build the prompt - typically
    ``excluded_ingredients_for_session(state["session_id"])`` - so this checks
    the model actually complied with the exclusion it was told about, not some
    independently-computed set."""
    db = ingredients_db if ingredients_db is not None else load_ingredients_db()
    available = {a.strip().lower() for a in (appliances or [])}
    excluded = excluded_ingredients or {}
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

        banned = sorted({ing.name.strip().lower() for ing in meal.ingredients} & excluded.keys())
        for ing_name in banned:
            reason = excluded.get(ing_name, "")
            reason_clause = f" ({reason})" if reason else ""
            problems.append(
                f"{tag}: uses '{ing_name}', which is banned for this session due to a prior "
                f"rejection{reason_clause} - do not use it, use an alternative instead."
            )

        extra = sorted({a for a in meal.appliances_used if a.strip().lower() not in available})
        if extra and available:
            problems.append(
                f"{tag}: uses appliances the household does not have: {extra} "
                f"(available: {sorted(available)})"
            )

        # Nutrition: any ingredient the calculator had to skip makes the meal
        # invalid (its numbers would be silently understated otherwise).
        nutrition_result = calculate_meal_nutrition(meal.ingredients, db)
        for reason in nutrition_result.skipped_ingredients:
            problems.append(f"{tag}: {_actionable_skip(reason)}")
        filled.append(
            meal.model_copy(update={"nutrition": nutrition_result.nutrition}).model_dump()
        )

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
        excluded_ingredients=excluded_ingredients_for_session(state.get("session_id") or ""),
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

    # no exclusion history -> identical result whether the arg is omitted,
    # None, or an explicit empty dict (this is the "unchanged behavior" case)
    r_empty = validate_meals(good, ["microwave", "stovetop"], expected_days=2, excluded_ingredients={})
    r_none = validate_meals(good, ["microwave", "stovetop"], expected_days=2, excluded_ingredients=None)
    assert r_empty.valid == r_none.valid == r.valid == True
    assert r_empty.feedback == r_none.feedback == r.feedback

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
    # skipped-ingredient now surfaces as an actionable problem, not just a warning
    assert any("not in the ingredient database" in p for p in r2.problems)
    assert r2.should_retry(0) is True
    assert r2.should_retry(MEAL_VALIDATION_MAX_RETRIES) is False

    # unit misuse: soy sauce is a real pantry ingredient but 'pcs' can't convert
    unit_bad = [
        {"name": "Soy bowl", "day_index": 0, "servings": 1,
         "ingredients": [{"name": "chicken breast", "quantity": 150, "unit": "g"},
                         {"name": "soy sauce", "quantity": 2, "unit": "pcs"}],
         "appliances_used": ["stovetop"], "estimated_prep_minutes": 15},
        {"name": "Tofu bowl", "day_index": 1, "servings": 1,
         "ingredients": [{"name": "firm tofu", "quantity": 150, "unit": "g"}],
         "appliances_used": ["stovetop"], "estimated_prep_minutes": 15},
    ]
    r3 = validate_meals(unit_bad, ["stovetop"], expected_days=2)
    print(r3.feedback)
    assert not r3.valid
    assert any("soy sauce" in p and "not convertible" in p and "tbsp" in p for p in r3.problems)
    assert not any("day coverage" in p for p in r3.problems)  # days are fine; only the unit is wrong
    assert r3.should_retry(0) is True

    # backstop: the model reintroduced a session-excluded ingredient (garlic).
    # Deterministic, no Bedrock call - the live forced-failure test (see the
    # task report) proves this against a REAL model slip; this proves the
    # checker + feedback wording in isolation.
    garlic_slip = [
        {"name": "Garlic Chicken Bowl", "day_index": 0, "servings": 1,
         "ingredients": [{"name": "chicken breast", "quantity": 150, "unit": "g"},
                         {"name": "garlic", "quantity": 2, "unit": "pcs"}],
         "appliances_used": ["stovetop"], "estimated_prep_minutes": 20},
        {"name": "Tofu bowl", "day_index": 1, "servings": 1,
         "ingredients": [{"name": "firm tofu", "quantity": 150, "unit": "g"}],
         "appliances_used": ["stovetop"], "estimated_prep_minutes": 15},
    ]
    r4 = validate_meals(
        garlic_slip, ["stovetop"], expected_days=2,
        excluded_ingredients={"garlic": "The user finds the garlic quantity excessive for their taste."},
    )
    print(r4.feedback)
    assert not r4.valid
    assert any(
        "garlic" in p and "banned for this session due to a prior rejection" in p
        and "garlic quantity excessive" in p and "use an alternative instead" in p
        for p in r4.problems
    ), r4.problems
    # the clean second meal must NOT be flagged just because garlic is banned somewhere in the plan
    assert not any("Tofu bowl" in p for p in r4.problems)
    assert r4.should_retry(0) is True

    print("decision_support_meals smoke test OK")

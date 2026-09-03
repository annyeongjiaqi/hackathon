"""Nutrition calculator (deterministic arithmetic, no LLM, no network).

Implements the "Extraction (nutrition count)" box: given a meal's ingredient
lines and a per-ingredient nutrition table, sum up calories / protein / carbs /
fats / fiber for the whole dish and return a ``Nutrition`` object to drop into
``Meal.nutrition``.

The nutrition table lives in ``agent/data/ingredients_db.json`` (a teammate is
building it). Until that file exists, ``load_ingredients_db()`` falls back to
``PLACEHOLDER_INGREDIENTS_DB`` below — same shape, ~a dozen common items — so
this module is testable now and needs no code change when the real file lands.

Expected JSON shape (per ingredient, keyed by lowercase name):
    {
      "chicken breast": {
        "per_100g": {"calories": 165, "protein_g": 31, "carbs_g": 0,
                     "fats_g": 3.6, "fiber_g": 0},
        "grams_per_unit": {"pcs": 170}      # optional, for non-mass units
      },
      ...
    }

Usage:
    >>> from agent.schemas import IngredientLine
    >>> lines = [IngredientLine(name="chicken breast", quantity=300, unit="g"),
    ...          IngredientLine(name="olive oil", quantity=1, unit="tbsp")]
    >>> calculate_meal_nutrition(lines)
    Nutrition(calories=..., protein_g=..., carbs_g=..., fats_g=..., fiber_g=...)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from agent.schemas import IngredientLine, Meal, Nutrition

# Where the real DB will live once the data teammate commits it.
INGREDIENTS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ingredients_db.json"

# Generic unit -> grams conversions used when an ingredient has no explicit
# ``grams_per_unit`` entry. "ml" assumes water-like density (~1 g/ml).
_GENERIC_UNIT_GRAMS: dict[str, float] = {
    "g": 1.0, "gram": 1.0, "grams": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "ml": 1.0, "l": 1000.0,
    "tsp": 5.0, "teaspoon": 5.0,
    "tbsp": 15.0, "tablespoon": 15.0,
    "cup": 240.0,
}

_NUTRIENT_KEYS = ("calories", "protein_g", "carbs_g", "fats_g", "fiber_g")

# --- Placeholder table (used only until agent/data/ingredients_db.json exists) --
PLACEHOLDER_INGREDIENTS_DB: dict[str, dict] = {
    "chicken breast": {"per_100g": {"calories": 165, "protein_g": 31.0, "carbs_g": 0.0, "fats_g": 3.6, "fiber_g": 0.0}, "grams_per_unit": {"pcs": 170}},
    "chicken thigh":  {"per_100g": {"calories": 209, "protein_g": 26.0, "carbs_g": 0.0, "fats_g": 10.9, "fiber_g": 0.0}, "grams_per_unit": {"pcs": 120}},
    "salmon":         {"per_100g": {"calories": 208, "protein_g": 20.0, "carbs_g": 0.0, "fats_g": 13.0, "fiber_g": 0.0}, "grams_per_unit": {"fillet": 150}},
    "firm tofu":      {"per_100g": {"calories": 144, "protein_g": 17.0, "carbs_g": 2.8, "fats_g": 8.7, "fiber_g": 2.3}, "grams_per_unit": {"block": 300}},
    "eggs":           {"per_100g": {"calories": 143, "protein_g": 13.0, "carbs_g": 1.1, "fats_g": 9.5, "fiber_g": 0.0}, "grams_per_unit": {"pcs": 50}},
    "white rice":     {"per_100g": {"calories": 130, "protein_g": 2.7, "carbs_g": 28.0, "fats_g": 0.3, "fiber_g": 0.4}},
    "brown rice":     {"per_100g": {"calories": 123, "protein_g": 2.7, "carbs_g": 25.6, "fats_g": 1.0, "fiber_g": 1.6}},
    "dried lentils":  {"per_100g": {"calories": 352, "protein_g": 24.6, "carbs_g": 63.4, "fats_g": 1.1, "fiber_g": 10.7}},
    "rolled oats":    {"per_100g": {"calories": 379, "protein_g": 13.2, "carbs_g": 67.7, "fats_g": 6.5, "fiber_g": 10.1}},
    "broccoli":       {"per_100g": {"calories": 34, "protein_g": 2.8, "carbs_g": 6.6, "fats_g": 0.4, "fiber_g": 2.6}, "grams_per_unit": {"head": 300}},
    "spinach":        {"per_100g": {"calories": 23, "protein_g": 2.9, "carbs_g": 3.6, "fats_g": 0.4, "fiber_g": 2.2}},
    "carrot":         {"per_100g": {"calories": 41, "protein_g": 0.9, "carbs_g": 9.6, "fats_g": 0.2, "fiber_g": 2.8}, "grams_per_unit": {"pcs": 60}},
    "onion":          {"per_100g": {"calories": 40, "protein_g": 1.1, "carbs_g": 9.3, "fats_g": 0.1, "fiber_g": 1.7}, "grams_per_unit": {"pcs": 110}},
    "canned chickpeas": {"per_100g": {"calories": 139, "protein_g": 7.0, "carbs_g": 22.0, "fats_g": 2.6, "fiber_g": 6.0}, "grams_per_unit": {"can": 240}},
    "olive oil":      {"per_100g": {"calories": 884, "protein_g": 0.0, "carbs_g": 0.0, "fats_g": 100.0, "fiber_g": 0.0}},
}

_db_cache: dict[str, dict] | None = None


def load_ingredients_db(path: Path = INGREDIENTS_DB_PATH, *, use_cache: bool = True) -> dict[str, dict]:
    """Load the nutrition table from JSON, or fall back to the placeholder.

    Swapping in the real data is zero-code: just commit the JSON file at
    ``path``. Pass ``use_cache=False`` in tests that patch the file.
    """
    global _db_cache
    if use_cache and _db_cache is not None:
        return _db_cache

    if path.exists():
        with path.open(encoding="utf-8") as fh:
            db = json.load(fh)
        db = {k.lower(): v for k, v in db.items()}
    else:
        db = PLACEHOLDER_INGREDIENTS_DB

    if use_cache:
        _db_cache = db
    return db


def _grams_for_line(line: IngredientLine, entry: dict | None) -> float | None:
    """Convert one ingredient line's quantity+unit to grams, or None if unknown."""
    unit = line.unit.strip().lower()
    if entry:
        per_unit = entry.get("grams_per_unit") or {}
        if unit in per_unit:
            return line.quantity * float(per_unit[unit])
        # allow a bare "pcs"/"unit" style key match on singular/plural
        for k, v in per_unit.items():
            if unit.rstrip("s") == k.rstrip("s"):
                return line.quantity * float(v)
    if unit in _GENERIC_UNIT_GRAMS:
        return line.quantity * _GENERIC_UNIT_GRAMS[unit]
    return None


def calculate_meal_nutrition(
    ingredients: Iterable[IngredientLine],
    ingredients_db: dict[str, dict] | None = None,
    *,
    strict: bool = False,
) -> Nutrition:
    """Sum nutrition across a meal's ingredient lines.

    Unknown ingredients or unconvertible units are skipped (or raise if
    ``strict=True``). Returns totals for the whole dish; divide by
    ``Meal.servings`` downstream for per-portion figures.
    """
    db = ingredients_db if ingredients_db is not None else load_ingredients_db()
    totals = {k: 0.0 for k in _NUTRIENT_KEYS}
    unresolved: list[str] = []

    for line in ingredients:
        entry = db.get(line.name.strip().lower())
        if not entry:
            unresolved.append(f"{line.name} (not in DB)")
            continue
        grams = _grams_for_line(line, entry)
        if grams is None:
            unresolved.append(f"{line.name} (unit '{line.unit}' not convertible)")
            continue
        scale = grams / 100.0
        per_100g = entry["per_100g"]
        for k in _NUTRIENT_KEYS:
            totals[k] += float(per_100g.get(k, 0.0)) * scale

    if unresolved and strict:
        raise ValueError(f"Cannot compute nutrition, unresolved: {unresolved}")

    return Nutrition(**{k: round(v, 1) for k, v in totals.items()})


def fill_meal_nutrition(meal: Meal, ingredients_db: dict[str, dict] | None = None) -> Meal:
    """Return a copy of ``meal`` with ``nutrition`` populated."""
    return meal.model_copy(update={"nutrition": calculate_meal_nutrition(meal.ingredients, ingredients_db)})


if __name__ == "__main__":  # smoke test
    lines = [
        IngredientLine(name="chicken breast", quantity=300, unit="g"),
        IngredientLine(name="brown rice", quantity=150, unit="g"),
        IngredientLine(name="broccoli", quantity=1, unit="head"),
        IngredientLine(name="olive oil", quantity=1, unit="tbsp"),
        IngredientLine(name="unicorn meat", quantity=100, unit="g"),  # unknown -> skipped
    ]
    n = calculate_meal_nutrition(lines)
    print("totals:", n.model_dump())
    assert n.calories > 700 and n.protein_g > 100  # 300g chicken alone is ~93g protein
    assert n.fiber_g > 0

    meal = Meal(
        name="Chicken, rice & broccoli",
        day_index=0,
        ingredients=lines[:4],
        estimated_prep_minutes=25,
    )
    filled = fill_meal_nutrition(meal)
    assert filled.nutrition is not None and meal.nutrition is None  # original untouched
    print("per serving (/2):", {k: round(v / meal.servings, 1) for k, v in filled.nutrition.model_dump().items()})

    try:
        calculate_meal_nutrition([IngredientLine(name="mystery", quantity=1, unit="g")], strict=True)
        raise AssertionError("strict mode should have raised")
    except ValueError as exc:
        print("strict raise OK:", exc)

    print("nutrition_calculator smoke test OK")

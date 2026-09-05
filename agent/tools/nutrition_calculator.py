"""Nutrition calculator (deterministic arithmetic, no LLM, no network).

Implements the "Extraction (nutrition count)" box: given a meal's ingredient
lines and a per-ingredient nutrition table, sum up calories / protein / carbs /
fats / fiber for the whole dish and return a ``Nutrition`` object to drop into
``Meal.nutrition``.

The nutrition table lives in ``agent/data/ingredients_db.json`` (owned by the
data team). If that file is missing, ``load_ingredients_db()`` falls back to
``PLACEHOLDER_INGREDIENTS_DB`` below so this module is still testable.

``load_ingredients_db()`` accepts either shape and normalises to the internal
one (``per_100g`` with ``fats_g``, optional ``grams_per_unit``):

  * internal / placeholder:
      {"chicken breast": {"per_100g": {"calories": 165, "protein_g": 31,
        "carbs_g": 0, "fats_g": 3.6, "fiber_g": 0}, "grams_per_unit": {"pcs": 170}}}
  * data-team file (``nutrition_per_100g`` + ``fat_g``, plus extra fields that
    are passed through untouched — ``cost_per_100g``, ``category``,
    ``substitutes``, ``shelf_life_days``); any top-level ``_meta`` key is dropped:
      {"chicken breast": {"category": "meat_poultry", "cost_per_100g": 1.5,
        "nutrition_per_100g": {"calories": 165, "protein_g": 31.0, "carbs_g": 0.0,
        "fat_g": 3.6, "fiber_g": 0.0, "sodium_mg": 74}, "shelf_life_days": 3,
        "substitutes": ["firm tofu", "canned tuna"]}}

Unit handling: weight ('g', 'kg', ...) and volume ('ml', 'tsp', 'tbsp', 'cup')
convert via a generic table; count units ('pcs', 'piece', 'clove', 'slice', ...)
convert via a per-ingredient typical-weight table. Anything still unconvertible
is NOT silently dropped: it is logged (WARNING) and returned in
``NutritionResult.skipped_ingredients``.

Usage:
    >>> from agent.schemas import IngredientLine
    >>> lines = [IngredientLine(name="chicken breast", quantity=300, unit="g"),
    ...          IngredientLine(name="egg", quantity=3, unit="pcs")]
    >>> result = calculate_meal_nutrition(lines)
    >>> result.nutrition
    Nutrition(calories=..., protein_g=..., carbs_g=..., fats_g=..., fiber_g=...)
    >>> result.skipped_ingredients
    []
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, NamedTuple

from agent.schemas import IngredientLine, Meal, Nutrition

logger = logging.getLogger(__name__)

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

# Count-based unit tokens meaning "one piece of <ingredient>". These have no
# fixed gram weight on their own — the weight depends on the ingredient, so they
# resolve against _COUNT_ITEM_GRAMS below.
_COUNT_UNITS = {
    "pcs", "pc", "piece", "pieces", "unit", "units",
    "ct", "count", "whole", "each", "ea",
    "stalk", "stalks",   # lemongrass / spring onion are naturally counted this way
}

# Count units that also imply a specific ingredient regardless of the line name
# (e.g. "2 cloves" -> garlic, "1 slice" -> bread).
_COUNT_UNIT_ALIASES = {
    "clove": "garlic", "cloves": "garlic",
    "slice": "bread", "slices": "bread",
    "fillet": "chicken breast", "fillets": "chicken breast",
    "can": "canned tuna", "cans": "canned tuna", "tin": "canned tuna", "tins": "canned tuna",
    "block": "firm tofu", "blocks": "firm tofu",
    "head": "broccoli", "heads": "broccoli",
}

# Typical edible weight of one piece, in grams. Keyed by ingredient name
# (lowercase). Covers every countable ingredient in ingredients_db.json plus the
# common generics recipes tend to express as "pcs"/"piece". Rough USDA-ish
# medium-size values — good enough for planning arithmetic.
_COUNT_ITEM_GRAMS: dict[str, float] = {
    # ingredients_db.json items
    "egg": 50.0,
    "eggs": 50.0,
    "garlic": 3.0,          # one clove
    "garlic clove": 3.0,
    "onion": 110.0,
    "tomato": 123.0,
    "carrot": 61.0,
    "sweet potato": 130.0,
    "broccoli": 300.0,      # one head
    "chicken breast": 174.0,
    "firm tofu": 300.0,     # one block
    "canned tuna": 142.0,   # one can, drained
    # 8-cuisine expansion: naturally-countable additions
    "spring onion": 15.0,   # one stalk
    "shallot": 25.0,
    "shallots": 25.0,
    "lemongrass": 20.0,     # one trimmed stalk
    "button mushrooms": 18.0,   # one mushroom
    "button mushroom": 18.0,
    "mushroom": 18.0,
    "mushrooms": 18.0,
    "salmon fillet": 140.0,
    "sirloin steak": 220.0,
    "prawns": 15.0,        # one medium peeled prawn
    "prawn": 15.0,
    "scallops": 20.0,     # one scallop
    "scallop": 20.0,
    # common generics
    "bread": 28.0,          # one sandwich slice
    "slice of bread": 28.0,
    "banana": 118.0,
    "apple": 182.0,
    "potato": 173.0,
    "bell pepper": 119.0,
    "capsicum": 119.0,
    "lemon": 58.0,
    "lime": 67.0,
    "lettuce": 300.0,       # one head
    "avocado": 150.0,
}

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


def _normalize_db_entry(entry: dict) -> dict:
    """Coerce a data-team entry into the internal shape; leave internal ones as-is.

    Idempotent. Unknown ``nutrition_per_100g`` keys (e.g. ``sodium_mg``) and all
    non-nutrition fields (``cost_per_100g``, ``category``, ``substitutes``, ...)
    are preserved so later flows can use them.
    """
    if "per_100g" in entry:
        return entry
    src = entry.get("nutrition_per_100g")
    if not isinstance(src, dict):
        return entry  # unrecognised shape; lookups against it will simply miss
    out = dict(entry)
    out["per_100g"] = {
        "calories": src.get("calories", 0.0),
        "protein_g": src.get("protein_g", 0.0),
        "carbs_g": src.get("carbs_g", 0.0),
        "fats_g": src.get("fats_g", src.get("fat_g", 0.0)),
        "fiber_g": src.get("fiber_g", 0.0),
    }
    return out


def load_ingredients_db(path: Path = INGREDIENTS_DB_PATH, *, use_cache: bool = True) -> dict[str, dict]:
    """Load the nutrition table (data-team file if present, else the placeholder),
    normalised to the internal shape.

    Pass ``use_cache=False`` in tests that patch the file.
    """
    global _db_cache
    if use_cache and _db_cache is not None:
        return _db_cache

    if path.exists() and path.stat().st_size > 0:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        db = {
            name.lower(): _normalize_db_entry(entry)
            for name, entry in raw.items()
            if not name.startswith("_") and isinstance(entry, dict)
        }
    else:
        db = PLACEHOLDER_INGREDIENTS_DB

    if use_cache:
        _db_cache = db
    return db


def _grams_for_line(line: IngredientLine, entry: dict | None) -> float | None:
    """Convert one ingredient line's quantity+unit to grams, or None if unknown.

    Order: explicit ``grams_per_unit`` on the DB entry -> generic weight/volume
    table -> count-unit table (per-ingredient typical piece weight).
    """
    unit = line.unit.strip().lower()
    name = line.name.strip().lower()

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

    # count-based units: unit implies a specific ingredient (clove -> garlic) ...
    if unit in _COUNT_UNIT_ALIASES:
        grams = _COUNT_ITEM_GRAMS.get(name) or _COUNT_ITEM_GRAMS.get(_COUNT_UNIT_ALIASES[unit])
        if grams is not None:
            return line.quantity * grams

    # ... or it's a generic "piece" and the ingredient itself has a piece weight
    if unit in _COUNT_UNITS:
        grams = _COUNT_ITEM_GRAMS.get(name)
        if grams is not None:
            return line.quantity * grams

    return None


class NutritionResult(NamedTuple):
    """Return type of :func:`calculate_meal_nutrition`.

    Tuple-compatible (``nutrition, skipped = calculate_meal_nutrition(...)``) so
    callers that only want the numbers can still unpack the first element.
    """

    nutrition: Nutrition
    skipped_ingredients: list[str]


def calculate_meal_nutrition(
    ingredients: Iterable[IngredientLine],
    ingredients_db: dict[str, dict] | None = None,
    *,
    strict: bool = False,
) -> NutritionResult:
    """Sum nutrition across a meal's ingredient lines.

    Ingredients that are not in the DB, or whose unit cannot be converted to
    grams, are left out of the totals AND reported: each is logged at WARNING
    and collected into ``NutritionResult.skipped_ingredients``. With
    ``strict=True`` any such ingredient raises ``ValueError`` instead.

    Totals are for the whole dish; divide by ``Meal.servings`` downstream for
    per-portion figures.
    """
    db = ingredients_db if ingredients_db is not None else load_ingredients_db()
    totals = {k: 0.0 for k in _NUTRIENT_KEYS}
    skipped: list[str] = []

    for line in ingredients:
        entry = db.get(line.name.strip().lower())
        if not entry:
            reason = f"{line.name} (not in ingredients DB)"
            skipped.append(reason)
            logger.warning("nutrition: skipped ingredient - %s", reason)
            continue
        grams = _grams_for_line(line, entry)
        if grams is None:
            reason = f"{line.name} (unit '{line.unit}' not convertible to grams)"
            skipped.append(reason)
            logger.warning("nutrition: skipped ingredient - %s", reason)
            continue
        scale = grams / 100.0
        per_100g = entry["per_100g"]
        for k in _NUTRIENT_KEYS:
            totals[k] += float(per_100g.get(k, 0.0)) * scale

    if skipped and strict:
        raise ValueError(f"Cannot compute nutrition, unresolved: {skipped}")

    nutrition = Nutrition(**{k: round(v, 1) for k, v in totals.items()})
    return NutritionResult(nutrition=nutrition, skipped_ingredients=skipped)


def fill_meal_nutrition(meal: Meal, ingredients_db: dict[str, dict] | None = None) -> Meal:
    """Return a copy of ``meal`` with ``nutrition`` populated.

    Unconvertible ingredients are logged (WARNING) by ``calculate_meal_nutrition``;
    the meal is still returned with best-effort totals.
    """
    result = calculate_meal_nutrition(meal.ingredients, ingredients_db)
    return meal.model_copy(update={"nutrition": result.nutrition})


if __name__ == "__main__":  # smoke test
    logging.basicConfig(level=logging.WARNING, format="  [warn] %(message)s")

    # legacy / placeholder shape still works when passed explicitly
    legacy = calculate_meal_nutrition(
        [IngredientLine(name="chicken breast", quantity=100, unit="g")],
        ingredients_db=PLACEHOLDER_INGREDIENTS_DB,
    )
    assert round(legacy.nutrition.calories) == 165 and round(legacy.nutrition.protein_g) == 31
    assert legacy.skipped_ingredients == []

    # default loader: data-team file if present, normalised
    db = load_ingredients_db(use_cache=False)
    assert "_meta" not in db and all("per_100g" in e for e in db.values())

    lines = [
        IngredientLine(name="chicken breast", quantity=300, unit="g"),
        IngredientLine(name="brown rice", quantity=150, unit="g"),
        IngredientLine(name="broccoli", quantity=150, unit="g"),
        IngredientLine(name="olive oil", quantity=15, unit="g"),
        IngredientLine(name="unicorn meat", quantity=100, unit="g"),  # unknown -> skipped + warned
    ]
    result = calculate_meal_nutrition(lines, ingredients_db=db)
    print("totals:", result.nutrition.model_dump())
    print("skipped:", result.skipped_ingredients)
    assert result.nutrition.calories > 600 and result.nutrition.protein_g > 90
    assert result.nutrition.fiber_g > 2  # from broccoli
    assert any("unicorn meat" in s for s in result.skipped_ingredients)

    # --- count-unit conversion: the Day-2 demo egg scramble --------------------
    # Previously "3 pcs egg" was silently skipped -> scramble showed ~8 g protein.
    scramble = [
        IngredientLine(name="egg", quantity=3, unit="pcs"),
        IngredientLine(name="spinach", quantity=100, unit="g"),
        IngredientLine(name="tomato", quantity=120, unit="g"),
        IngredientLine(name="onion", quantity=80, unit="g"),
        IngredientLine(name="sweet potato", quantity=150, unit="g"),
        IngredientLine(name="olive oil", quantity=12, unit="ml"),
    ]
    sr = calculate_meal_nutrition(scramble, ingredients_db=db)
    print("egg scramble:", sr.nutrition.model_dump(), "| skipped:", sr.skipped_ingredients)
    assert sr.skipped_ingredients == []                       # nothing silently dropped
    assert sr.nutrition.protein_g > 15, sr.nutrition.protein_g  # realistic, not ~8 g
    # cross-check: 3 eggs alone (3 * 50 g) should supply most of that protein
    eggs_only = calculate_meal_nutrition(
        [IngredientLine(name="egg", quantity=3, unit="pcs")], ingredients_db=db
    )
    assert round(eggs_only.nutrition.protein_g) == round(3 * 0.5 * db["egg"]["per_100g"]["protein_g"])

    # cloves -> garlic, slices -> bread
    clove = calculate_meal_nutrition(
        [IngredientLine(name="garlic", quantity=2, unit="cloves")], ingredients_db=db
    )
    assert clove.nutrition.calories > 0 and clove.skipped_ingredients == []

    meal = Meal(
        name="Tomato spinach egg scramble",
        day_index=0,
        ingredients=scramble,
        estimated_prep_minutes=20,
    )
    filled = fill_meal_nutrition(meal)
    assert filled.nutrition is not None and meal.nutrition is None  # original untouched
    assert filled.nutrition.protein_g > 15
    print("per serving (/2):", {k: round(v / meal.servings, 1) for k, v in filled.nutrition.model_dump().items()})

    # --- still-unconvertible: reported, not hidden ---------------------------
    weird = calculate_meal_nutrition(
        [IngredientLine(name="broccoli", quantity=100, unit="g"),
         IngredientLine(name="dragon fruit", quantity=2, unit="pcs"),   # not in DB at all
         IngredientLine(name="soy sauce", quantity=1, unit="pcs")],     # in DB, count unit has no piece weight
        ingredients_db=db,
    )
    assert any("dragon fruit" in s for s in weird.skipped_ingredients)
    assert any("soy sauce" in s and "not convertible" in s for s in weird.skipped_ingredients)
    assert weird.nutrition.calories > 0  # broccoli still counted

    try:
        calculate_meal_nutrition([IngredientLine(name="mystery", quantity=1, unit="g")], strict=True)
        raise AssertionError("strict mode should have raised")
    except ValueError as exc:
        print("strict raise OK:", exc)

    print("nutrition_calculator smoke test OK")

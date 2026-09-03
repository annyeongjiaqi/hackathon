"""Offline nutrition arithmetic. Runtime never calls an external service."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Iterable, Mapping

DB_PATH = Path(__file__).parents[1] / "data" / "ingredients_db.json"
NUTRIENTS = ("calories", "protein_g", "carbs_g", "fat_g", "fibre_g", "sodium_mg", "potassium_mg")


def load_ingredient_db(path: str | Path = DB_PATH) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {key: value for key, value in raw.items() if not key.startswith("_")}


def calculate_nutrition(ingredients: Iterable[Mapping], database: Mapping | None = None) -> dict:
    db = database or load_ingredient_db()
    totals = {key: 0.0 for key in NUTRIENTS}
    missing: list[str] = []
    missing_nutrients: set[str] = set()
    for line in ingredients:
        name = str(line["name"]).strip().lower()
        record = db.get(name)
        if record is None:
            missing.append(name)
            continue
        factor = float(line["quantity"]) / 100.0
        nutrition = record.get("nutrition_per_100g", {})
        for key in NUTRIENTS:
            source_key = "fiber_g" if key == "fibre_g" and "fibre_g" not in nutrition else key
            if source_key not in nutrition or nutrition[source_key] is None:
                missing_nutrients.add(f"{name}: {key}")
                continue
            totals[key] += float(nutrition[source_key]) * factor
    return {"nutrition": {k: round(v, 2) for k, v in totals.items()}, "missing_ingredients": sorted(set(missing)), "missing_nutrients": sorted(missing_nutrients)}


def add_nutrition_to_meal(meal: dict, database: Mapping | None = None) -> dict:
    result = calculate_nutrition(meal.get("ingredients", []), database)
    return {**meal, "nutrition": result["nutrition"], "missing_ingredients": result["missing_ingredients"], "missing_nutrients": result["missing_nutrients"]}

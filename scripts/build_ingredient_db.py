"""Build the runtime DB from a USDA FoodData Central JSON export.

Download Foundation or SR Legacy JSON from https://fdc.nal.usda.gov/download-datasets.html,
then run: python scripts/build_ingredient_db.py path/to/FoodData_Central.json
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

NUTRIENT_MAP = {1008: "calories", 1003: "protein_g", 1005: "carbs_g", 1004: "fat_g", 1079: "fibre_g", 1093: "sodium_mg", 1092: "potassium_mg"}


def build(source: Path, manifest: Path, output: Path) -> None:
    wanted = json.loads(manifest.read_text())["ingredients"]
    raw = json.loads(source.read_text())
    foods = raw if isinstance(raw, list) else next((raw[k] for k in ("FoundationFoods", "SRLegacyFoods", "foods") if k in raw), [])
    result = {}
    for food in foods:
        name = food.get("description", "").lower()
        key = next((w for w in wanted if w in name), None)
        if not key or key in result:
            continue
        values = {v: 0.0 for v in NUTRIENT_MAP.values()}
        for row in food.get("foodNutrients", []):
            nutrient = row.get("nutrient", {})
            number = nutrient.get("number") or nutrient.get("id")
            if int(number or 0) in NUTRIENT_MAP:
                values[NUTRIENT_MAP[int(number)]] = row.get("amount", 0)
        result[key] = {"fdc_id": food.get("fdcId"), "nutrition_per_100g": values, "prices_per_100g": {"default": 0}}
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("agent/data/ingredient_manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("agent/data/ingredients_db.json"))
    args = parser.parse_args()
    build(args.source, args.manifest, args.output)

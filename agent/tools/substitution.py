"""Reusable deterministic substitution candidates for rejection and budget fixes."""

SUBSTITUTIONS = {
    "salmon": ["chicken breast", "tofu"], "chicken breast": ["tofu", "canned chickpeas"],
    "beef": ["chicken breast", "tofu"], "fresh spinach": ["frozen spinach"],
    "bell pepper": ["carrot"], "quinoa": ["brown rice"], "prawns": ["tofu"],
}


def suggest_substitution(name: str, dietary_restrictions: list[str] | None = None, database: dict | None = None) -> str | None:
    restrictions = " ".join(dietary_restrictions or []).lower()
    for candidate in SUBSTITUTIONS.get(name.lower(), []):
        if "vegetarian" in restrictions and candidate in {"chicken breast", "beef", "salmon", "prawns"}:
            continue
        return candidate
    return None


def replace_ingredient(meal: dict, original: str, replacement: str) -> dict:
    ingredients = [{**i, "name": replacement if i["name"].lower() == original.lower() else i["name"]} for i in meal["ingredients"]]
    return {**meal, "ingredients": ingredients}

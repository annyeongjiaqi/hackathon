"""Shared substitution tool (deterministic / rule-based, no LLM).

One implementation, two callers (roadmap "Shared substitution tool"):
  * Creative(grocery) budget-overrun path — swap a costly item for a cheaper one
  * Rejection handling (preference-fixable branch) — swap a disliked ingredient

Given an ingredient and a free-text reason, classify the reason, then pick a
substitute from ``SUBSTITUTIONS`` whose tags satisfy the goal (and any hard
dietary restrictions). Falls back to "another item from the same aisle" with low
confidence when the ingredient isn't in the table. The caller decides whether to
accept based on ``confidence``.

Kept rule-based on purpose for the hackathon build. A later version could route
the fallback case through Personalized (LLM) — the signature wouldn't change.

Usage:
    >>> suggest_substitute("ribeye steak", "we are over budget this week").substitute
    'chicken thigh'
    >>> suggest_substitute("chicken breast", "I don't eat meat, I'm vegetarian").substitute
    'firm tofu'
    >>> suggest_substitute("milk", "lactose intolerant").substitute
    'oat milk'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent.schemas import IngredientLine

ReasonCategory = Literal["cheaper", "preference", "dietary", "constraint", "generic"]

# --- reason classification --------------------------------------------------
_REASON_KEYWORDS: list[tuple[ReasonCategory, tuple[str, ...]]] = [
    ("cheaper", ("budget", "expensive", "cheaper", "cheap", "cost", "pricey", "afford", "price")),
    ("dietary", ("vegan", "vegetarian", "gluten", "lactose", "dairy-free", "dairy free",
                 "allergy", "allergic", "halal", "kosher", "pescatarian", "nut-free", "nut free")),
    ("preference", ("dislike", "don't like", "dont like", "do not like", "hate", "not a fan",
                    "prefer", "too spicy", "bland", "sick of", "tired of", "boring")),
    ("constraint", ("no oven", "no stove", "no blessing", "equipment", "too long", "too time",
                    "no time", "portion", "too much", "too little", "servings")),
]

# tag vocabulary: cheaper | vegetarian | vegan | gluten_free | dairy_free | leaner | faster
_SUBSTITUTIONS: dict[str, list[dict]] = {
    "ribeye steak": [
        {"name": "chicken thigh", "tags": {"cheaper", "leaner"}, "note": "~1/3 the price per gram of protein"},
        {"name": "firm tofu", "tags": {"cheaper", "vegetarian", "vegan", "leaner"}, "note": "cheapest protein swap"},
    ],
    "beef mince": [
        {"name": "turkey mince", "tags": {"cheaper", "leaner"}, "note": "leaner, usually a bit cheaper"},
        {"name": "brown lentils", "tags": {"cheaper", "vegetarian", "vegan"}, "note": "1:1 in bolognese-style dishes"},
    ],
    "salmon": [
        {"name": "mackerel", "tags": {"cheaper"}, "note": "similar omega-3 profile, cheaper"},
        {"name": "canned tuna", "tags": {"cheaper"}, "note": "shelf-stable, much cheaper"},
    ],
    "chicken breast": [
        {"name": "chicken thigh", "tags": {"cheaper"}, "note": "cheaper, more forgiving to cook"},
        {"name": "firm tofu", "tags": {"vegetarian", "vegan", "cheaper"}, "note": "press and cube, same cook method"},
    ],
    "prawns": [
        {"name": "firm white fish", "tags": {"cheaper"}, "note": "cheaper per gram, same quick sear"},
    ],
    "pine nuts": [
        {"name": "sunflower seeds", "tags": {"cheaper"}, "note": "toast them; a fraction of the price"},
    ],
    "milk": [
        {"name": "oat milk", "tags": {"dairy_free", "vegan"}, "note": "closest texture for cooking/coffee"},
    ],
    "butter": [
        {"name": "olive oil", "tags": {"dairy_free", "vegan"}, "note": "use ~3/4 the volume"},
    ],
    "greek yogurt": [
        {"name": "coconut yogurt", "tags": {"dairy_free", "vegan"}, "note": "unsweetened variety"},
    ],
    "pasta": [
        {"name": "gluten-free pasta", "tags": {"gluten_free"}, "note": "cook 1-2 min less, salt the water well"},
    ],
    "soy sauce": [
        {"name": "tamari", "tags": {"gluten_free"}, "note": "same flavour, no wheat"},
    ],
    "cream": [
        {"name": "cashew cream", "tags": {"dairy_free", "vegan"}, "note": "blend soaked cashews with water"},
    ],
}

_GOAL_TAG: dict[ReasonCategory, set[str]] = {
    "cheaper": {"cheaper"},
    "dietary": {"vegan", "vegetarian", "gluten_free", "dairy_free"},
    "preference": set(),   # any substitute is fine for a plain dislike
    "constraint": set(),
    "generic": set(),
}

# hard restriction keyword -> tag the substitute MUST carry
_RESTRICTION_TAG: dict[str, str] = {
    "vegan": "vegan", "vegetarian": "vegetarian",
    "gluten": "gluten_free", "celiac": "gluten_free", "coeliac": "gluten_free",
    "lactose": "dairy_free", "dairy": "dairy_free",
}


@dataclass
class SubstitutionSuggestion:
    original: str
    substitute: str | None
    reason_category: ReasonCategory
    rationale: str
    confidence: float          # 0.0-1.0; caller decides whether to auto-apply
    found: bool                # True if from the curated table, False if generic fallback


def classify_reason(reason: str) -> ReasonCategory:
    """Bucket a free-text reason string."""
    low = reason.lower()
    for category, keywords in _REASON_KEYWORDS:
        if any(kw in low for kw in keywords):
            return category
    return "generic"


def _required_tags(reason: str) -> set[str]:
    low = reason.lower()
    return {tag for kw, tag in _RESTRICTION_TAG.items() if kw in low}


def suggest_substitute(
    ingredient: str,
    reason: str,
    *,
    category_lookup=None,
) -> SubstitutionSuggestion:
    """Suggest one substitute for ``ingredient`` given a free-text ``reason``.

    ``category_lookup`` is injectable for testing; defaults to
    ``supermarket_lookup.get_category`` for the generic fallback.
    """
    name = ingredient.strip().lower()
    category = classify_reason(reason)
    goal_tags = _GOAL_TAG[category]
    must_have = _required_tags(reason)

    candidates = _SUBSTITUTIONS.get(name, [])
    # filter by hard restrictions first
    if must_have:
        candidates = [c for c in candidates if must_have <= c["tags"]]
    # then prefer ones that match the goal
    ranked = (
        [c for c in candidates if goal_tags & c["tags"]] + [c for c in candidates if not (goal_tags & c["tags"])]
        if goal_tags
        else candidates
    )

    if ranked:
        pick = ranked[0]
        matches_goal = bool(goal_tags & pick["tags"]) or not goal_tags
        return SubstitutionSuggestion(
            original=ingredient,
            substitute=pick["name"],
            reason_category=category,
            rationale=f"{reason.strip()} -> swap to {pick['name']}: {pick['note']}.",
            confidence=0.85 if matches_goal else 0.6,
            found=True,
        )

    # --- generic fallback: point at the same aisle ------------------------
    if category_lookup is None:
        from agent.tools.supermarket_lookup import get_category as category_lookup  # lazy import
    aisle = category_lookup(ingredient)
    return SubstitutionSuggestion(
        original=ingredient,
        substitute=None,
        reason_category=category,
        rationale=(
            f"No curated swap for '{ingredient}'. Try another {aisle} item that fits "
            f"the recipe role; reason was: {reason.strip()}."
        ),
        confidence=0.25,
        found=False,
    )


def substitute_in_ingredient_line(line: IngredientLine, reason: str) -> tuple[IngredientLine, SubstitutionSuggestion]:
    """Apply a suggestion to an ``IngredientLine``, keeping quantity + unit.

    Returns the (possibly unchanged) line and the suggestion so the caller can
    inspect ``confidence`` / ``found``.
    """
    suggestion = suggest_substitute(line.name, reason)
    if suggestion.substitute is None:
        return line, suggestion
    return line.model_copy(update={"name": suggestion.substitute}), suggestion


if __name__ == "__main__":  # smoke test
    s1 = suggest_substitute("ribeye steak", "we are way over budget this week")
    print(s1)
    assert s1.reason_category == "cheaper" and s1.substitute == "chicken thigh" and s1.found

    s2 = suggest_substitute("chicken breast", "I'm vegetarian and don't eat meat")
    print(s2)
    assert s2.substitute == "firm tofu" and "vegetarian" in _SUBSTITUTIONS["chicken breast"][1]["tags"]

    s3 = suggest_substitute("milk", "I'm lactose intolerant")
    print(s3)
    assert s3.substitute == "oat milk" and s3.reason_category == "dietary"

    s4 = suggest_substitute("pasta", "celiac, no gluten for me")
    assert s4.substitute == "gluten-free pasta"

    s5 = suggest_substitute("dragonfruit", "too expensive", category_lookup=lambda _n: "produce")
    print(s5)
    assert s5.substitute is None and s5.found is False and s5.confidence < 0.3

    line = IngredientLine(name="ribeye steak", quantity=400, unit="g")
    new_line, sug = substitute_in_ingredient_line(line, "over budget")
    assert new_line.name == "chicken thigh" and new_line.quantity == 400 and new_line.unit == "g"
    assert line.name == "ribeye steak"  # original untouched
    print("substitution smoke test OK")

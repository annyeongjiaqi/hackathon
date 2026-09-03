"""Pydantic models for the LLM nodes' structured outputs.

``ChatBedrockConverse(...).with_structured_output(Model)`` returns an already-
validated instance of these, so downstream nodes never parse raw text.

Two structured outputs:
  * ``MealPlan``    — from Creative(meals)
  * ``GroceryList`` — from Creative(grocery), fired on the deferred trigger
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class IngredientLine(BaseModel):
    """One ingredient with the amount this recipe uses."""

    name: str = Field(description="Ingredient name, lowercase, matching the ingredient DB where possible")
    quantity: float = Field(gt=0, description="Amount used in this meal")
    unit: str = Field(description="Unit for quantity, e.g. 'g', 'ml', 'pcs', 'tbsp'")


class Nutrition(BaseModel):
    """Per-meal nutrition totals for the whole dish (all servings combined).

    NOTE: this is normally filled in *after* generation by the deterministic
    nutrition calculator (arithmetic over the ingredient DB), not by the LLM.
    It is optional on ``Meal`` so the model is not pushed to invent numbers.
    """

    calories: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    fats_g: float = Field(ge=0)
    fiber_g: float = Field(ge=0)


# ---------------------------------------------------------------------------
# Creative(meals) output
# ---------------------------------------------------------------------------


class Meal(BaseModel):
    name: str = Field(description="Dish name")
    day_index: int = Field(ge=0, description="0-based day this meal is planned for")
    servings: int = Field(default=2, ge=1, description="DINK couple: defaults to 2")
    ingredients: list[IngredientLine] = Field(min_length=1)
    steps: list[str] = Field(default_factory=list, description="Short ordered cooking steps")
    appliances_used: list[str] = Field(
        default_factory=list,
        description="Appliances this recipe needs; must be a subset of the user's appliances",
    )
    estimated_prep_minutes: int = Field(ge=0, description="Rough total hands-on + cook time")
    nutrition: Nutrition | None = Field(
        default=None,
        description="Leave null; populated downstream by the nutrition calculator",
    )
    status: Literal["pending", "finished", "rejected"] = Field(
        default="pending",
        description="Lifecycle status; new meals are always 'pending'",
    )


class MealDraft(BaseModel):
    """The LLM-authored part of a meal - everything except downstream-computed
    fields. Used for scoped single-meal regeneration so the model is never asked
    to emit ``nutrition`` (which trips structured-output validation) or ``status``.
    """

    name: str = Field(description="Dish name")
    day_index: int = Field(ge=0, description="0-based day this meal is planned for")
    servings: int = Field(default=2, ge=1)
    ingredients: list[IngredientLine] = Field(min_length=1)
    steps: list[str] = Field(default_factory=list, description="Short ordered cooking steps")
    appliances_used: list[str] = Field(
        default_factory=list,
        description="Appliances this recipe needs; must be a subset of the user's appliances",
    )
    estimated_prep_minutes: int = Field(ge=0, description="Rough total hands-on + cook time")

    def to_meal_dict(self) -> dict:
        """As a ``Meal``-shaped dict with the computed fields left for downstream."""
        return {**self.model_dump(), "nutrition": None, "status": "pending"}


class MealPlan(BaseModel):
    """Full structured output of Creative(meals).

    Used for the initial full plan and for scoped regenerations (in the scoped
    case the node keeps only the meals it asked for).
    """

    meals: list[Meal] = Field(min_length=1)
    notes: str = Field(default="", description="Optional one-line rationale for the plan")


# ---------------------------------------------------------------------------
# Creative(grocery) output
# ---------------------------------------------------------------------------


class GroceryItem(BaseModel):
    name: str = Field(description="Ingredient name, lowercase, matching the ingredient DB where possible")
    quantity: float = Field(
        ge=0,
        description="Amount the plan needs (aggregated across all meals); may be 0 only for an "
        "already_have item",
    )
    unit: str = Field(description="Unit for quantity, e.g. 'g', 'ml', 'pcs'")
    category: str = Field(
        default="other",
        description="Supermarket section, e.g. 'produce', 'meat', 'dairy', 'pantry'",
    )
    estimated_cost: float = Field(ge=0, description="Estimated price for this line item, in local currency")
    already_have: bool = Field(
        default=False,
        description="True if covered by tracked leftover_ingredients and should not be bought",
    )


class GroceryList(BaseModel):
    """Full structured output of Creative(grocery).

    ``estimated_total_cost`` / ``within_budget`` are the model's first estimate;
    Decision-Support(grocery) recomputes the total deterministically and, if it
    is over budget, sends feedback back for a substitution pass.
    """

    items: list[GroceryItem] = Field(min_length=1)
    estimated_total_cost: float = Field(ge=0)
    within_budget: bool = Field(description="Model's own check of total vs. the user's budget")
    shopping_day_index: int = Field(ge=0, description="Day index this shop covers")


# ---------------------------------------------------------------------------
# Extraction(rejection reason) output
# ---------------------------------------------------------------------------


class RejectionAssessment(BaseModel):
    """Structured output of the Extraction(rejection reason) node.

    Two branches only (roadmap "Rejection Handling: Two Branches, Not One"):
    a preference complaint that a single swap might fix, versus a recipe that is
    fundamentally wrong for this household and needs full regeneration.
    """

    category: Literal["preference_fixable", "constraint_violated"] = Field(
        description=(
            "'preference_fixable': dislikes a specific ingredient, the spice level, or the "
            "cuisine - a substitution could plausibly fix it. "
            "'constraint_violated': the recipe cannot work for this household as written - "
            "needs equipment they do not have, takes too long, or the portion size is wrong."
        )
    )
    reason_summary: str = Field(
        description="One short, neutral sentence capturing what the user objected to."
    )
    target_ingredient: str | None = Field(
        default=None,
        description=(
            "Only for a 'preference_fixable' complaint about one ingredient: the single "
            "ingredient name to swap out, copied exactly (lowercase) from the rejected "
            "meal's ingredient list. Null for spice-level/cuisine complaints or when "
            "'constraint_violated'."
        ),
    )

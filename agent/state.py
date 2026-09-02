"""LangGraph state schema for the meal-planner agent.

One ``MealPlanState`` object is threaded through all 4 triggered flows
(initial generation, remaining-meals regeneration, single-meal regeneration,
grocery-list generation). The LangGraph checkpointer (InMemorySaver + thread_id
= session id) persists this between triggers within a session. It is *not* the
long-term store — ``learned_preferences`` is the only field that mirrors
DynamoDB, and it is loaded in at session start.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def append(existing: list | None, incoming: Any) -> list:
    """Reducer for the ``log`` field: accumulate instead of overwrite.

    Accepts either a single entry or a list of entries so nodes can do
    ``{"log": "did X"}`` or ``{"log": ["did X", "did Y"]}``.
    """
    result = list(existing) if existing else []
    if incoming is None:
        return result
    if isinstance(incoming, list):
        result.extend(incoming)
    else:
        result.append(incoming)
    return result


class MealPlanState(TypedDict, total=False):
    # --- Onboarding (fixed for the session) --------------------------------
    shopping_frequency_days: int
    budget: float
    supermarket: str
    appliances: list[str]
    goal: str
    cuisine_preferences: list[str]
    dietary_restrictions: list[str]
    living_alone_or_partner: str

    # --- Meals ------------------------------------------------------------
    meals: list[dict]          # each: name, ingredients, day_index, status, nutrition
    meals_valid: bool          # set by Decision-Support(meals)
    meals_feedback: str        # why the last attempt failed, fed back to Creative(meals)
    meals_retry_count: int     # bounded by MEAL_VALIDATION_MAX_RETRIES

    # --- Grocery (generated on the deferred trigger only) ----------------
    grocery_list: list[dict]
    grocery_valid: bool
    grocery_feedback: str
    grocery_retry_count: int   # bounded by GROCERY_VALIDATION_MAX_RETRIES

    # --- Leftover tracking (cheap state updates, no LLM call) ------------
    leftover_ingredients: list[str]
    days_until_next_shopping: int

    # --- Rejection handling --------------------------------------------------
    rejection_reason_raw: str   # the user's typed reason, verbatim
    rejection_category: str     # "preference" | "constraint"
    rejected_meal_index: int    # which entry in `meals` is being reworked

    # --- Long-term memory (mirror of DynamoDB, loaded at session start) --
    learned_preferences: list[str]

    # --- Run log (reducer-accumulated) ---------------------------------------
    log: Annotated[list, append]

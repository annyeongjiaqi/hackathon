"""Extraction(rejection reason) — LLM node that classifies a rejected meal.

Part of the rejection-handling flow (one of the roadmap's 4 triggered flows,
separate from the meal/grocery generation pipeline). It reads the user's typed
reason for rejecting one meal and produces a ``RejectionAssessment``:

  * ``preference_fixable``   -> a swap might fix it   (router tries substitution)
  * ``constraint_violated``  -> recipe is fundamentally wrong (router regenerates)

Classification + a short extracted summary only. The DynamoDB write of the
learned reason (Personalized node) is a separate task and is NOT done here.
"""

from __future__ import annotations

import json

from langchain_aws import ChatBedrockConverse

from agent.constants import (
    AWS_REGION,
    DEFAULT_MODEL_ID,
    REJECTION_EXTRACTION_MAX_TOKENS,
    REJECTION_EXTRACTION_TEMPERATURE,
)
from agent.schemas import RejectionAssessment
from agent.state import MealPlanState

SYSTEM_PROMPT = (
    "You classify why a user rejected one meal from their plan, into exactly one of two "
    "buckets:\n"
    "  preference_fixable  - they dislike a specific ingredient, the spice level, or the "
    "cuisine style. A single ingredient swap or seasoning change could plausibly satisfy them.\n"
    "  constraint_violated - the recipe does not fit their kitchen or life as written: it "
    "needs equipment they lack, takes too long, or makes the wrong number of servings. A swap "
    "will not fix this; the meal has to be rebuilt.\n"
    "Also give one neutral sentence summarising the objection, and - only when the complaint "
    "is about one specific ingredient - name that ingredient exactly as it appears in the "
    "meal's ingredient list."
)


def build_messages(state: MealPlanState) -> list[tuple[str, str]]:
    meals = state.get("meals") or []
    idx = int(state.get("rejected_meal_index") or 0)
    rejected = meals[idx] if 0 <= idx < len(meals) else {}
    raw_reason = (state.get("rejection_reason_raw") or "").strip()

    meal_view = {
        "name": rejected.get("name"),
        "ingredients": rejected.get("ingredients"),
        "appliances_used": rejected.get("appliances_used"),
        "estimated_prep_minutes": rejected.get("estimated_prep_minutes"),
        "servings": rejected.get("servings"),
    }
    available_appliances = state.get("appliances") or []

    human = "\n".join(
        [
            "Rejected meal:",
            json.dumps(meal_view, indent=2),
            "",
            f"Appliances the household actually has: {available_appliances or 'unknown'}",
            "",
            f'User\'s typed reason for rejecting it: "{raw_reason}"',
            "",
            "Classify it (preference_fixable vs constraint_violated), summarise the objection "
            "in one sentence, and set target_ingredient only if a single named ingredient is "
            "the problem (copy the name exactly from the ingredient list above; otherwise null).",
        ]
    )
    return [("system", SYSTEM_PROMPT), ("human", human)]


def _build_model():
    return ChatBedrockConverse(
        model=DEFAULT_MODEL_ID,
        region_name=AWS_REGION,
        temperature=REJECTION_EXTRACTION_TEMPERATURE,
        max_tokens=REJECTION_EXTRACTION_MAX_TOKENS,
    )


def extraction_rejection_node(state: MealPlanState) -> dict:
    """LangGraph node: classify the rejection, return a partial state update."""
    assessment: RejectionAssessment = (
        _build_model().with_structured_output(RejectionAssessment).invoke(build_messages(state))
    )
    target = (assessment.target_ingredient or "").strip().lower() or ""
    return {
        "rejection_category": assessment.category,
        "rejection_reason_summary": assessment.reason_summary,
        "rejection_target_ingredient": target,
        "log": [
            f"extraction_rejection: {assessment.category}"
            + (f" (ingredient: {target})" if target else "")
            + f" - {assessment.reason_summary}"
        ],
    }


if __name__ == "__main__":  # prompt-only smoke test (no Bedrock call)
    demo: MealPlanState = {
        "appliances": ["microwave", "stovetop"],
        "rejected_meal_index": 0,
        "rejection_reason_raw": "way too much garlic for me",
        "meals": [
            {"name": "Garlic Chicken with Broccoli", "day_index": 0,
             "ingredients": [{"name": "chicken breast", "quantity": 150, "unit": "g"},
                             {"name": "broccoli", "quantity": 200, "unit": "g"},
                             {"name": "garlic", "quantity": 6, "unit": "pcs"}],
             "appliances_used": ["stovetop"], "estimated_prep_minutes": 30, "servings": 1},
        ],
    }
    for role, content in build_messages(demo):
        print(f"----- {role} -----\n{content}\n")

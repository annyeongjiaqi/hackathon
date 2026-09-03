"""LangGraph wiring for the initial meal-plan flow (roadmap flow #1 of 4).

    creative_meals -> decision_support_meals -> (valid? END : retry creative_meals)

Retry is bounded by MEAL_VALIDATION_MAX_RETRIES; once exhausted the graph ends
with the last attempt rather than looping forever (roadmap Key Risk #3).

An InMemorySaver checkpointer is attached so this graph already shares session
state the way the other three flows will (thread_id = user session id). The
DynamoDB-backed long-term memory is deliberately NOT wired here yet.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent.constants import MEAL_VALIDATION_MAX_RETRIES
from agent.nodes.creative_meals import creative_meals_node
from agent.nodes.decision_support_meals import decision_support_meals_node
from agent.state import MealPlanState


def route_after_validation(state: MealPlanState) -> str:
    """Loop back to Creative(meals) on failure until the retry budget is spent."""
    if state.get("meals_valid"):
        return "end"
    if int(state.get("meals_retry_count") or 0) < MEAL_VALIDATION_MAX_RETRIES:
        return "retry"
    return "end"  # give up: accept the last attempt


def build_meal_plan_graph(checkpointer: InMemorySaver | None = None):
    graph = StateGraph(MealPlanState)
    graph.add_node("creative_meals", creative_meals_node)
    graph.add_node("decision_support_meals", decision_support_meals_node)

    graph.add_edge(START, "creative_meals")
    graph.add_edge("creative_meals", "decision_support_meals")
    graph.add_conditional_edges(
        "decision_support_meals",
        route_after_validation,
        {"retry": "creative_meals", "end": END},
    )

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# Module-level compiled graph for the entrypoint / callers to import.
MEAL_PLAN_GRAPH = build_meal_plan_graph()


if __name__ == "__main__":  # end-to-end demo — makes a real Bedrock call
    import json
    import uuid

    from dotenv import load_dotenv

    load_dotenv()

    sample_onboarding: MealPlanState = {
        "budget": 55.0,
        "goal": "high protein (~130g/day), around 1800 kcal/day, more vegetables and fibre",
        "dietary_restrictions": ["no pork", "no shellfish"],
        "appliances": ["microwave", "stovetop"],
        "cuisine_preferences": ["mediterranean", "japanese"],
        "living_alone_or_partner": "alone",
        "shopping_frequency_days": 4,
        "supermarket": "FairPrice",
        "leftover_ingredients": [],
        "meals_retry_count": 0,
    }

    config = {"configurable": {"thread_id": f"demo-{uuid.uuid4().hex[:8]}"}}
    print(f"Invoking meal-plan graph (thread_id={config['configurable']['thread_id']}) ...\n")

    final_state = MEAL_PLAN_GRAPH.invoke(sample_onboarding, config=config)

    print("=== run log ===")
    for entry in final_state.get("log", []):
        print(" -", entry)

    print(f"\n=== meals_valid: {final_state.get('meals_valid')} ===")
    if not final_state.get("meals_valid"):
        print("final validation feedback:")
        print(final_state.get("meals_feedback"))

    print("\n=== validated MealPlan ===")
    for meal in final_state.get("meals", []):
        n = meal.get("nutrition") or {}
        ings = ", ".join(f"{i['quantity']}{i['unit']} {i['name']}" for i in meal["ingredients"])
        print(f"\nDay {meal['day_index']}: {meal['name']}  ({meal.get('estimated_prep_minutes', '?')} min, "
              f"{meal.get('servings')} serving(s))")
        print(f"  ingredients: {ings}")
        print(f"  appliances : {meal.get('appliances_used')}")
        if n:
            print(f"  nutrition  : {n['calories']:.0f} kcal | {n['protein_g']:.0f}g protein | "
                  f"{n['carbs_g']:.0f}g carbs | {n['fats_g']:.0f}g fat | {n['fiber_g']:.0f}g fibre")

    print("\n=== raw JSON ===")
    print(json.dumps(final_state.get("meals", []), indent=2))

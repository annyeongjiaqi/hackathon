"""LangGraph wiring for the meal-plan + grocery-list pipeline (roadmap flows #1 and #2).

    START
      -> creative_meals
      -> decision_support_meals
           -> (invalid & retries left)  loop back to creative_meals
           -> (valid, or retries spent) creative_grocery
      -> creative_grocery
      -> decision_support_grocery
           -> (over budget & retries left) loop back to creative_grocery
           -> (within budget, or retries spent) END

Both retry loops are bounded (MEAL_VALIDATION_MAX_RETRIES /
GROCERY_VALIDATION_MAX_RETRIES); when a budget is spent the graph moves on with
the last attempt rather than looping forever (roadmap Key Risk #3).

An InMemorySaver checkpointer is attached so all flows share session state
(thread_id = user session id). DynamoDB long-term memory and the rejection /
Personalized flows are deliberately NOT wired here yet.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent.constants import GROCERY_VALIDATION_MAX_RETRIES, MEAL_VALIDATION_MAX_RETRIES
from agent.nodes.creative_grocery import creative_grocery_node
from agent.nodes.creative_meals import creative_meals_node
from agent.nodes.decision_support_grocery import decision_support_grocery_node
from agent.nodes.decision_support_meals import decision_support_meals_node
from agent.state import MealPlanState


def route_after_meal_validation(state: MealPlanState) -> str:
    """Retry Creative(meals) while invalid and the retry budget holds, else proceed."""
    if state.get("meals_valid"):
        return "proceed"
    if int(state.get("meals_retry_count") or 0) < MEAL_VALIDATION_MAX_RETRIES:
        return "retry"
    return "proceed"  # retries spent: move on with the last attempt


def route_after_grocery_validation(state: MealPlanState) -> str:
    """Retry Creative(grocery) while over budget and the retry budget holds, else end."""
    if state.get("grocery_valid"):
        return "end"
    if int(state.get("grocery_retry_count") or 0) < GROCERY_VALIDATION_MAX_RETRIES:
        return "retry"
    return "end"  # retries spent: accept the last attempt


def build_meal_plan_graph(checkpointer: InMemorySaver | None = None):
    graph = StateGraph(MealPlanState)
    graph.add_node("creative_meals", creative_meals_node)
    graph.add_node("decision_support_meals", decision_support_meals_node)
    graph.add_node("creative_grocery", creative_grocery_node)
    graph.add_node("decision_support_grocery", decision_support_grocery_node)

    graph.add_edge(START, "creative_meals")
    graph.add_edge("creative_meals", "decision_support_meals")
    graph.add_conditional_edges(
        "decision_support_meals",
        route_after_meal_validation,
        {"retry": "creative_meals", "proceed": "creative_grocery"},
    )
    graph.add_edge("creative_grocery", "decision_support_grocery")
    graph.add_conditional_edges(
        "decision_support_grocery",
        route_after_grocery_validation,
        {"retry": "creative_grocery", "end": END},
    )

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# Module-level compiled graph for the entrypoint / callers to import.
MEAL_PLAN_GRAPH = build_meal_plan_graph()


if __name__ == "__main__":  # end-to-end demo — makes real Bedrock calls
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
        "leftover_ingredients": ["olive oil"],   # demo: shows already_have cross-reference
        "days_until_next_shopping": 0,
        "meals_retry_count": 0,
        "grocery_retry_count": 0,
    }

    config = {"configurable": {"thread_id": f"demo-{uuid.uuid4().hex[:8]}"}}
    print(f"Invoking meal-plan + grocery graph (thread_id={config['configurable']['thread_id']}) ...\n")

    final_state = MEAL_PLAN_GRAPH.invoke(sample_onboarding, config=config)

    print("=== run log ===")
    for entry in final_state.get("log", []):
        print(" -", entry)

    print(f"\n=== meals_valid: {final_state.get('meals_valid')} ===")
    if not final_state.get("meals_valid"):
        print("final meal feedback:", final_state.get("meals_feedback"))

    print("\n--- validated MealPlan ---")
    from agent.schemas import Meal
    from agent.tools.nutrition_calculator import calculate_meal_nutrition

    for meal in final_state.get("meals", []):
        n = meal.get("nutrition") or {}
        ings = ", ".join(f"{i['quantity']}{i['unit']} {i['name']}" for i in meal["ingredients"])
        skipped = calculate_meal_nutrition(Meal(**meal).ingredients).skipped_ingredients
        print(f"\nDay {meal['day_index']}: {meal['name']}  "
              f"({meal.get('estimated_prep_minutes', '?')} min, {meal.get('servings')} serving(s))")
        print(f"  ingredients: {ings}")
        print(f"  appliances : {meal.get('appliances_used')}")
        if n:
            print(f"  nutrition  : {n['calories']:.0f} kcal | {n['protein_g']:.0f}g protein | "
                  f"{n['carbs_g']:.0f}g carbs | {n['fats_g']:.0f}g fat | {n['fiber_g']:.0f}g fibre")
        print(f"  skipped_ingredients: {skipped or '[]  (all ingredients counted)'}")

    print(f"\n=== grocery_valid: {final_state.get('grocery_valid')} ===")
    print("grocery feedback:", final_state.get("grocery_feedback"))

    print("\n--- validated / priced / categorized GroceryList ---")
    by_category: dict[str, list[dict]] = {}
    total = 0.0
    for item in final_state.get("grocery_list", []):
        by_category.setdefault(item.get("category", "other"), []).append(item)
        if not item.get("already_have"):
            total += float(item.get("estimated_cost") or 0.0)
    for category in sorted(by_category):
        print(f"\n[{category}]")
        for item in by_category[category]:
            flag = "  (already have)" if item.get("already_have") else ""
            print(f"  {item['quantity']}{item['unit']:<4} {item['name']:<20} "
                  f"${float(item.get('estimated_cost') or 0.0):>6.2f}{flag}")
    print(f"\n  estimated total (excl. already-have): ${total:.2f}  /  budget ${sample_onboarding['budget']:.2f}")

    print("\n=== raw grocery JSON ===")
    print(json.dumps(final_state.get("grocery_list", []), indent=2))

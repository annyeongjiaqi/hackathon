"""LangGraph orchestration for four user-triggered flows."""
from __future__ import annotations
from langgraph.graph import END, START, StateGraph
from agent.checkpointer import checkpointer
from agent.constants import MEAL_VALIDATION_MAX_RETRIES
from agent.nodes.creative_grocery import create_grocery
from agent.nodes.creative_meals import create_meals
from agent.nodes.extraction_rejection import classify_rejection
from agent.nodes.personalized import propose_substitution, save_preference
from agent.state import MealPlanState
from agent.tools.nutrition_calculator import add_nutrition_to_meal
from agent.tools.substitution import replace_ingredient


def rejection_route(category: str) -> str:
    return "substitute" if category == "preference" else "regenerate"


def _valid_meals(meals: list[dict], state: dict) -> tuple[bool, str]:
    restrictions = " ".join(state.get("dietary_restrictions", [])).lower()
    appliances = set(a.lower() for a in state.get("appliances", []))
    for meal in meals:
        if any(i.get("unit") != "g" for i in meal.get("ingredients", [])): return False, "Every quantity must be grams"
        if not set(meal.get("appliances_used", [])).issubset(appliances): return False, "Unsupported appliance"
        names = " ".join(i["name"] for i in meal["ingredients"])
        if "vegetarian" in restrictions and any(x in names for x in ("chicken", "salmon", "beef")): return False, "Dietary restriction violated"
    return True, ""


def dispatch(state: MealPlanState) -> dict:
    action = state.get("action", "status")
    if action == "initial_generation":
        meals, retries, valid, feedback = [], 0, False, ""
        while not valid and retries < MEAL_VALIDATION_MAX_RETRIES:
            meals = create_meals(state); valid, feedback = _valid_meals(meals, state); retries += 1
        grocery = create_grocery({**state, "meals": meals})
        return {"meals": meals, "meals_valid": valid, "meals_feedback": feedback, "meals_retry_count": retries, "grocery_list": grocery["items"], "grocery_valid": grocery["valid"], "grocery_feedback": ", ".join(grocery["missing_prices"]), "estimated_grocery_cost": grocery["total_cost"], "grocery_list_status": "estimated", "log": "Generated initial meal plan and provisional grocery list"}
    if action == "report_leftovers":
        today = int(state.get("current_day", 1)); meals = list(state.get("meals", []))
        if 0 <= int(state.get("finished_meal_index", -1)) < len(meals): meals[int(state["finished_meal_index"])] = {**meals[int(state["finished_meal_index"])], "status": "finished"}
        remaining_days = [d for d in range(today + 1, int(state["shopping_frequency_days"]) + 1)]
        retained = [m for m in meals if int(m["day_index"]) <= today]
        regenerated = create_meals(state, remaining_days) if remaining_days else []
        return {"meals": retained + regenerated, "leftover_ingredients": state.get("new_leftovers", state.get("leftover_ingredients", [])), "days_until_next_shopping": max(0, int(state["shopping_frequency_days"]) - today), "log": "Updated leftovers and remaining meals; grocery deferred"}
    if action == "reject_meal":
        idx = int(state["rejected_meal_index"]); reason = state["rejection_reason_raw"]; extraction = classify_rejection(reason)
        learned = list(state.get("learned_preferences", [])); learned.append(reason); save_preference(state.get("session_id", "anonymous"), reason)
        if rejection_route(extraction["category"]) == "substitute":
            suggestion = propose_substitution(state["meals"][idx], state.get("dietary_restrictions", []))
            if suggestion: return {"rejection_category": "preference", "learned_preferences": learned, "substitute_suggestion": suggestion, "substitute_attempted": True, "log": "Saved preference and proposed substitution"}
        meals = list(state["meals"]); meals[idx] = create_meals(state, [meals[idx]["day_index"]])[0]
        return {"meals": meals, "rejection_category": extraction["category"], "learned_preferences": learned, "substitute_suggestion": {}, "log": "Saved rejection and regenerated one meal"}
    if action == "substitute_response":
        idx = int(state["rejected_meal_index"]); meals = list(state["meals"]); suggestion = state.get("substitute_suggestion", {})
        if state.get("substitute_accepted") and suggestion:
            meals[idx] = add_nutrition_to_meal(replace_ingredient(meals[idx], suggestion["original_ingredient"], suggestion["substitute_ingredient"]))
        else: meals[idx] = create_meals(state, [meals[idx]["day_index"]])[0]
        return {"meals": meals, "substitute_suggestion": {}, "log": "Applied substitute response and recalculated nutrition"}
    if action in ("preview_grocery", "generate_grocery"):
        result = create_grocery(state)
        status = "estimated" if action == "preview_grocery" else "final"
        return {"grocery_list": result["items"], "grocery_valid": result["valid"], "grocery_feedback": ", ".join(result["missing_prices"]), "grocery_retry_count": 0, "estimated_grocery_cost": result["total_cost"], "grocery_list_status": status, "log": f"Generated {status} grocery list"}
    return {}


builder = StateGraph(MealPlanState)
builder.add_node("dispatch", dispatch)
builder.add_edge(START, "dispatch"); builder.add_edge("dispatch", END)
graph = builder.compile(checkpointer=checkpointer)


def invoke(payload: dict, session_id: str) -> dict:
    return graph.invoke({**payload, "session_id": session_id}, config={"configurable": {"thread_id": session_id}})

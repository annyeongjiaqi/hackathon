from agent.graph import rejection_route
from agent.tools.budget_validator import validate_budget
from agent.tools.nutrition_calculator import calculate_nutrition, load_ingredient_db
from agent.tools.supermarket_lookup import lookup_price
from agent.nodes.creative_meals import _fallback
from agent.graph import invoke
from agent.tools.shelf_life_rules import get_freshness_tier


def test_shelf_life_tier():
    assert get_freshness_tier(7, 28) == "fresh"
    assert get_freshness_tier(8, 28) == "shelf_stable"
    assert get_freshness_tier(7, 7) == "fresh"


def test_nutrition_calculation():
    result = calculate_nutrition([{"name": "tofu", "quantity": 200}])
    assert result["nutrition"]["calories"] == 152
    assert result["nutrition"]["potassium_mg"] == 242


def test_missing_ingredients_are_not_invented():
    result = calculate_nutrition([{"name": "moon cheese", "quantity": 100}])
    assert result["missing_ingredients"] == ["moon cheese"]
    assert result["nutrition"]["calories"] == 0


def test_budget_validation():
    result = validate_budget([{"name": "salmon", "quantity": 100, "unit": "g"}], 2, "default")
    assert not result["valid"] and result["over_by"] == 1.2


def test_rejection_routing():
    assert rejection_route("preference") == "substitute"
    assert rejection_route("constraint") == "regenerate"


def test_supplied_database_aliases_and_missing_values():
    database = {
        "item": {
            "cost_per_100g": 2.5,
            "nutrition_per_100g": {
                "calories": 10, "protein_g": 1, "carbs_g": 2,
                "fat_g": 0, "fiber_g": 3, "sodium_mg": 4,
            },
        }
    }
    result = calculate_nutrition([{"name": "item", "quantity": 100}], database)
    assert result["nutrition"]["fibre_g"] == 3
    assert "item: potassium_mg" in result["missing_nutrients"]
    assert lookup_price("item", 200, "FairPrice", database)["estimated_cost"] == 5


def test_offline_fallback_respects_changed_settings():
    state = {
        "shopping_frequency_days": 3, "budget": 40,
        "appliances": ["microwave"], "dietary_restrictions": [],
        "cuisine_preferences": ["western"], "living_alone_or_partner": "alone",
    }
    meals = _fallback(state, [1, 2])
    assert all(set(m["appliances_used"]) <= {"microwave"} for m in meals)
    assert all(m["servings"] == 1 for m in meals)


def test_database_metadata_is_not_an_ingredient():
    assert "_meta" not in load_ingredient_db()


def test_default_profile_graph_integration(monkeypatch):
    """HTTP handler equivalent: the payload reaches the same graph invocation."""
    monkeypatch.setenv("USE_BEDROCK", "false")
    payload = {
        "action": "initial_generation", "shopping_frequency_days": 7,
        "budget": 100.0, "supermarket": "FairPrice", "appliances": ["stove"],
        "goal": "healthier eating", "cuisine_preferences": ["asian"],
        "dietary_restrictions": [], "living_alone_or_partner": "alone",
        "days_until_next_shopping": 7,
    }
    result = invoke(payload, "integration-default-profile")
    assert len(result["meals"]) == 7
    assert all(meal["appliances_used"] == ["stove"] for meal in result["meals"])
    assert len({meal["name"] for meal in result["meals"]}) > 1
    assert result["grocery_list"]
    assert result["grocery_list_status"] == "estimated"


def test_location_fields_survive_state(monkeypatch):
    monkeypatch.setenv("USE_BEDROCK", "false")
    payload = {
        "action": "initial_generation", "shopping_frequency_days": 3,
        "budget": 60.0, "supermarket": "FairPrice", "postal_code": "560123",
        "preferred_store": "FairPrice Ang Mo Kio Hub", "appliances": ["stove"],
        "goal": "reduce waste", "cuisine_preferences": ["asian"],
        "dietary_restrictions": [], "living_alone_or_partner": "alone",
    }
    result = invoke(payload, "integration-location-fields")
    assert result["postal_code"] == "560123"
    assert result["preferred_store"] == "FairPrice Ang Mo Kio Hub"


def test_missing_price_item_remains_visible():
    result = validate_budget([{"name": "unknown herb", "quantity": 25, "unit": "g"}], 20, "FairPrice")
    assert result["items"][0]["name"] == "unknown herb"
    assert result["items"][0]["estimated_cost"] is None

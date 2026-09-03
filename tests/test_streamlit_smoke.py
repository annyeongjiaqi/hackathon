from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_onboarding_form_renders_with_submit_button():
    app = AppTest.from_file(str(Path(__file__).parents[1] / "streamlit_app.py"))
    app.run(timeout=10)
    assert not app.exception
    assert any(button.label == "Make my meal plan" for button in app.button)
    assert any(select.label == "Nearest FairPrice outlet" for select in app.selectbox)


def test_nearest_store_uses_coordinates():
    from agent.tools.fairprice_locator import nearest_store

    stores = [
        {"name": "far", "latitude": 1.45, "longitude": 103.90},
        {"name": "near", "latitude": 1.334, "longitude": 103.722},
    ]
    store, distance = nearest_store(stores, 1.3338632, 103.7219932)
    assert store["name"] == "near"
    assert distance < 0.1


def test_fairprice_catalogue_contains_only_physical_outlets():
    import json

    path = Path(__file__).parents[1] / "agent" / "data" / "fairprice_stores.json"
    stores = json.loads(path.read_text())
    assert len(stores) >= 150
    required = {"id", "name", "type", "postal_code", "address", "zone", "latitude", "longitude", "is_24_hour"}
    assert all(required <= store.keys() for store in stores)
    assert all(store["type"] in {"FairPrice", "FairPrice Finest", "FairPrice Xtra"} for store in stores)
    assert all("click and collect" not in store["name"].lower() for store in stores)
    assert all(len(store["postal_code"]) == 6 and store["postal_code"].isdigit() for store in stores)

# Waste-Less Meal Planner

A Streamlit meal planner backed by a local AgentCore-compatible LangGraph server. It generates meal plans, reacts to leftovers and rejected meals, calculates nutrition without runtime network calls, and defers the grocery list until shopping day is close.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m agent.entrypoint
```

In a second terminal:

```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```

AWS credentials must grant Bedrock Converse access to the model in `agent/constants.py`. For a no-AWS demo, set `USE_BEDROCK=false`; the checked-in reference recipes exercise the complete UI and deterministic pipeline. Create a DynamoDB table named `user_preferences` with string partition key `session_id` to enable cross-session memory.

## Nutrition database

Runtime reads `agent/data/ingredients_db.json` only. To rebuild it, download a USDA FoodData Central Foundation or SR Legacy JSON archive, extract it, then run:

```bash
python scripts/build_ingredient_db.py /path/to/FoodData_Central.json
```

Review/add local supermarket prices after generation; USDA does not provide Singapore retail prices.

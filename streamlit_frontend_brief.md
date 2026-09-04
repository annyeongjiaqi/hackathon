# Streamlit Frontend Build Brief — Agentic Meal Planner

*Give this whole document to your LLM (Codex or otherwise) as the spec for building `streamlit_app.py`. It only needs to build the frontend — the agent backend is already built, tested, and running as a local HTTP server. Read "Before you start coding" at the end before generating anything — it matters.*

## 1. What this app is

An AI agent plans a full multi-day meal plan and a priced, categorized shopping list for a user based on their budget, nutritional goal, kitchen appliances, dietary restrictions, and shopping frequency. The user can reject a specific meal and say why (too much garlic, no oven, etc.) — the agent fixes that meal and remembers the reason for future plans. Your job is the Streamlit UI only: forms, the weekly calendar, the reject/swap interaction, the shopping list, and a nutrition progress view — all talking to the already-built agent over HTTP.

## 2. Backend connection

- Base URL (local dev): `http://localhost:8080`
- Endpoint: `POST /invocations`
- Header: `Content-Type: application/json`
- Every request must include `session_id` and `action`.
- **Generate one `session_id` per user session** (e.g. a UUID stored in `st.session_state` the first time the app loads) and **reuse the same one for every call that user makes** — this is literally how the agent remembers their rejections and prior plans. A new `session_id` means a blank slate.

## 3. The two actions that actually work right now

### `action: "initial_generation"`

This is the onboarding submission. Request body — map these fields directly to your onboarding form:

```json
{
  "session_id": "<uuid, generated once per user>",
  "action": "initial_generation",
  "shopping_frequency_days": 7,
  "budget": 55.0,
  "supermarket": "FairPrice",
  "postal_code": "560123",
  "preferred_store": "FairPrice Xtra Ang Mo Kio Hub",
  "appliances": ["microwave", "stovetop"],
  "goal": "high protein, around 1800 kcal/day, more vegetables and fibre",
  "cuisine_preferences": ["mediterranean", "japanese"],
  "dietary_restrictions": ["no pork", "no shellfish"],
  "living_alone_or_partner": "alone",
  "days_until_next_shopping": 4
}
```

Confirmed valid values seen in testing: `shopping_frequency_days` includes at least 7, 14, and 28 (the app has 4 selectable options total — confirm the exact list with your teammate before building the dropdown). `appliances` is a plain array of strings — an empty-ish kitchen is `["microwave"]` only, don't assume stovetop/oven are always present.

Response:

```json
{
  "meals": [ /* array of Meal objects, see below */ ],
  "grocery_list": [ /* array of GroceryItem objects, see below */ ],
  "meals_valid": true,
  "grocery_valid": true,
  "meals_feedback": "",
  "grocery_feedback": "",
  "log": [ /* internal trace strings, don't show to the user */ ]
}
```

### `action: "reject_meal"`

Fires when the user rejects a meal and (optionally) types why. Don't resend the plan — the server already has it via `session_id`.

```json
{
  "session_id": "<same uuid as before>",
  "action": "reject_meal",
  "rejected_meal_index": 0,
  "rejection_reason_raw": "way too much garlic for me"
}
```

Response includes the updated `meals` (re-render the calendar from this), plus `rejection_category`, `rejection_reason_summary`, `rejection_target_ingredient`, `rejection_outcome`, and `learned_preferences`. You probably don't need to display these directly — they're mostly for your own debugging — but `learned_preferences` could power a nice "the agent remembers: no garlic" chip somewhere in the UI if you want it.

### Everything else — not built yet, don't call it

`preview_grocery`, `generate_grocery`, `report_leftovers`, `substitute_response` all currently return `{"error": "not_implemented", ...}`. Don't build UI flows that depend on any of these.

## 4. Meal and GroceryItem shapes — confirmed fields, and what to double-check

Confirmed from real backend responses: a **Meal** has a `name`, `ingredients`, a `steps` field with the actual cooking instructions (this exists and is populated — make sure the UI has *somewhere* to show it, even if it's a collapsed "how to cook this" expander rather than on the main card), and a nutrition object with `calories`, `protein_g`, `carbs_g`, `fats_g`, `fiber_g`. A **GroceryItem** has `name`, `quantity`, `unit`, `category`, `estimated_cost`, and `already_have` (true/false — already-owned items are priced at $0 but still show their real needed quantity, don't hide them from the list).

**Important — do not build against old mock field names.** If any earlier mock data or prior UI code used `fat_g`, `fibre_g`, `sodium_mg`, or a potassium field, or a different shape for `missing_ingredients`/`substitute_suggestion` — those are wrong. The real backend uses `fats_g` and `fiber_g` and does not return sodium or potassium at all. The exact current shape of `missing_ingredients` and `substitute_suggestion` hasn't been pinned down in what's been reported so far — see "Before you start coding" below, don't guess this one.

## 5. States the UI must handle honestly

`meals_valid` and `grocery_valid` can come back `false` even after the backend already retried internally — this happens for genuinely infeasible requests (e.g. an unrealistically low budget). When that happens, `meals_feedback`/`grocery_feedback` will explain what couldn't be satisfied. **Show this to the user plainly** ("we couldn't quite hit your budget — here's the closest we got, and why") rather than assuming every response represents full success. Don't build a UI that only has a happy path.

## 6. Required features (build in this order)

1. **Onboarding form** — matches the `initial_generation` fields above exactly.
2. **Weekly meal calendar** — one card per day/meal: name, nutrition summary, and access to the full cooking steps (expander or detail view). No real food photo API — use a placeholder image or icon per meal, don't build image generation/lookup.
3. **Reject/swap interaction** — a reject button + free-text reason input on each meal card, calling `reject_meal`, then re-rendering the calendar from the response.
4. **Shopping list view** — grouped by `category`, per-item `estimated_cost`, already-have items shown at $0 but not hidden, running total vs. the user's budget.
5. **Nutrition progress indicator** — daily/weekly % of goal met, computed client-side from the returned per-meal nutrition. (How the target values themselves are derived — e.g. from the free-text `goal` field — hasn't been detailed anywhere reported; if the UI needs concrete target numbers rather than just totals, that's a question for your teammate, not something to invent.)

## 7. Explicitly out of scope — don't build these

Real grocery price APIs, real photo-based ingredient recognition, any ML-based preference learning on the frontend (all of that is backend, already done), and anything wired to the four not-implemented actions above.

## 8. Before you start coding — do this first

Ask your teammate for one real, complete example JSON response for **both** `initial_generation` and `reject_meal` (they've already run these live dozens of times during testing — this is a five-second copy-paste for them, from their terminal or test logs) and hand those to Codex alongside this document as ground-truth examples. This document describes the fields that are confirmed, but a couple of nested shapes (`missing_ingredients`, `substitute_suggestion`) aren't fully pinned down here — a real example response removes all guesswork and stops Codex from inventing a plausible-looking but wrong schema. Five minutes now saves a broken integration later.

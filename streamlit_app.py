"""Streamlit frontend for the agentic meal planner.

This UI intentionally calls only the two backend actions currently confirmed by the
frontend brief: ``initial_generation`` and ``reject_meal``.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from html import escape
from pathlib import Path

import requests
import streamlit as st

from agent.constants import AGENT_SERVER_URL
from agent.tools.fairprice_locator import geocode_postal_code, nearest_store


FAIRPRICE_STORES_PATH = Path(__file__).parent / "agent" / "data" / "fairprice_stores.json"


@st.cache_data
def load_fairprice_stores() -> list[dict]:
    """Load the bundled FairPrice outlet catalogue when available."""
    try:
        return json.loads(FAIRPRICE_STORES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def store_label(store: dict) -> str:
    return f'{store["type"]} · {store["name"]} · {store["postal_code"]}'


def ranked_stores(postal_code: str) -> list[dict]:
    """Rank exact/postal-sector matches without pretending they are GPS distance."""
    postal = postal_code.strip()
    return sorted(
        load_fairprice_stores(),
        key=lambda store: (
            0
            if postal and store.get("postal_code") == postal
            else 1
            if len(postal) >= 2 and str(store.get("postal_code", "")).startswith(postal[:2])
            else 2,
            store.get("type", ""),
            store.get("name", ""),
        ),
    )


def autoselect_nearest_fairprice() -> None:
    """Resolve a six-digit postal code and preselect the nearest FairPrice outlet."""
    postal = st.session_state.get("setup_v6_postal_code", "").strip()
    st.session_state["nearest_store_message"] = ""
    if len(postal) != 6 or not postal.isdigit():
        return

    stores = load_fairprice_stores()
    if not stores:
        return

    try:
        coordinates = geocode_postal_code(postal)
        if not coordinates:
            raise ValueError("Postal code was not found")
        store, distance = nearest_store(stores, *coordinates)
        st.session_state["setup_v6_preferred_store"] = store_label(store)
        st.session_state["nearest_store_message"] = (
            f"Nearest outlet: {store_label(store)} · about {distance:g} km away"
        )
    except (requests.RequestException, ValueError, KeyError, TypeError):
        ranked = ranked_stores(postal)
        if ranked:
            st.session_state["setup_v6_preferred_store"] = store_label(ranked[0])
        st.session_state["nearest_store_message"] = (
            "Live postal lookup was unavailable. We selected the closest postal-sector "
            "match; you can change it below."
        )


st.set_page_config(page_title="Good Enough to Eat", page_icon="🍅", layout="wide")
st.session_state.setdefault("session_id", str(uuid.uuid4()))
st.session_state.setdefault("agent_state", {})
st.session_state.setdefault("request_error", "")

UI_STATE_VERSION = 6
if st.session_state.get("ui_state_version") != UI_STATE_VERSION:
    for key in list(st.session_state):
        if key.startswith(("plan_", "setup_")) or key in {
            "main_view",
            "nearest_store_message",
            "day_filter",
        }:
            del st.session_state[key]
    st.session_state.ui_state_version = UI_STATE_VERSION


st.markdown(
    """
    <style>
    :root { --cream:#F6F2E8; --forest:#18392B; --sage:#DCE7D8; --coral:#E85D3F; --ink:#20251F; }
    .stApp { background:var(--cream); color:var(--ink); }
    [data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer { display:none!important; }
    [data-testid="stAppViewContainer"] > .main .block-container { max-width:1180px; padding-top:1rem; padding-bottom:5rem; }
    h1,h2,h3 { color:var(--forest)!important; letter-spacing:-.025em; }
    h1 { font-family:Georgia, 'Times New Roman', serif!important; font-size:clamp(2.65rem,4.2vw,3.35rem)!important; line-height:1.02!important; font-weight:500!important; margin:.15rem 0 .55rem!important; }
    p, label, [data-testid="stMetricLabel"] { color:var(--ink)!important; }
    .brandbar { display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid #18392b33; padding:.25rem 0 .8rem; margin-bottom:1.35rem; }
    .brand { color:var(--forest); font-family:Georgia,serif; font-size:1.22rem; font-weight:700; }
    .session { color:#4d675a; font-size:.78rem; letter-spacing:.04em; text-transform:uppercase; }
    .eyebrow { color:var(--coral); font-size:.75rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; margin-bottom:.55rem; }
    .lede { color:#4d5c52!important; font-size:1.08rem; max-width:700px; line-height:1.65; }
    .summary { background:var(--forest); color:white; padding:1.2rem 1.35rem; margin:1rem 0 1.8rem; border-left:7px solid var(--coral); }
    .summary strong { color:white; font-size:1.04rem; }
    .summary span { color:#dce7d8; }
    .day-card { background:#fff; border:1px solid #18392b24; border-top:4px solid var(--forest); padding:1.25rem 1.35rem .85rem; margin:.35rem 0 .8rem; box-shadow:0 7px 22px #18392b0b; }
    .day-no { color:var(--coral); font-size:.72rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
    .meal-title { color:var(--forest); font-family:Georgia,serif; font-size:1.55rem; line-height:1.15; margin:.4rem 0 .6rem; }
    .meal-meta { color:#53675b; font-size:.86rem; margin-bottom:.6rem; }
    .meal-icon { font-size:2rem; line-height:1; margin:.2rem 0 .7rem; }
    .ingredient { padding:.25rem 0; border-bottom:1px dotted #b7c7b9; color:#293b31; }
    .nutrition-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:.35rem; margin:.85rem 0 .4rem; }
    .nutrient { background:var(--sage); padding:.55rem .6rem; text-align:center; }
    .nutrient b { display:block; color:var(--forest); font-size:1rem; }
    .nutrient small { color:#506158; font-size:.65rem; text-transform:uppercase; letter-spacing:.06em; }
    .list-kicker { color:var(--coral); font-size:.72rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
    .grocery-row { background:#fff; border-bottom:1px solid #d5ded3; padding:.8rem .9rem; }
    .grocery-row strong { color:var(--forest); }
    .grocery-meta { color:#65746a; font-size:.82rem; margin-top:.18rem; }
    .already-have { color:#47705b; font-weight:700; }
    .feedback-note { background:#fff7e8; border-left:4px solid var(--coral); padding:.9rem 1rem; margin:.8rem 0 1rem; color:#4d4a3f; }
    div.stButton > button, div.stFormSubmitButton > button { background:#fff!important; border:1px solid #718b7c!important; border-radius:2px; color:var(--forest)!important; font-weight:700; min-height:2.7rem; opacity:1!important; }
    div.stButton > button:hover, div.stFormSubmitButton > button:hover { background:var(--sage)!important; border-color:var(--forest)!important; color:var(--forest)!important; }
    div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] { background:var(--coral)!important; border-color:var(--coral)!important; color:#fff!important; }
    div.stButton > button[kind="primary"]:hover, div.stFormSubmitButton > button[kind="primary"]:hover { background:#c94931!important; border-color:#c94931!important; color:#fff!important; }
    div[data-testid="stExpander"] { background:white; border:1px solid #18392b24; border-radius:2px; }
    div[data-testid="stMetric"] { background:var(--sage); border:none; padding:.65rem .75rem; }
    div[data-baseweb="select"] > div, [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea { background:#fff!important; border-color:#9cad9e!important; border-radius:2px!important; color:var(--ink)!important; -webkit-text-fill-color:var(--ink)!important; opacity:1!important; }
    [data-testid="stForm"] { background:#fff; border-color:#b8c6b8!important; border-radius:2px!important; padding:1.2rem!important; }
    @media(max-width:700px){
      [data-testid="stAppViewContainer"] > .main .block-container{padding:1rem 1rem 3rem!important}
      h1{font-size:2.45rem!important;line-height:1.02!important}
      .brandbar{align-items:flex-start;gap:.7rem;margin-bottom:1rem}.session{display:none}
      .lede{font-size:.98rem;line-height:1.5}.nutrition-strip{grid-template-columns:repeat(2,1fr)}
      [data-testid="column"]{min-width:100%!important;width:100%!important;flex:1 1 100%!important}
      [data-testid="stForm"]{padding:.85rem!important}
      div.stButton > button, div.stFormSubmitButton > button{width:100%!important;min-height:3rem}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def comma_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def profile() -> dict:
    """Return the exact onboarding fields expected by initial_generation."""
    return {
        "shopping_frequency_days": int(st.session_state.get("setup_v6_frequency", 7)),
        "budget": float(st.session_state.get("setup_v6_budget", 55.0)),
        "supermarket": st.session_state.get("setup_v6_supermarket", "FairPrice").strip(),
        "postal_code": st.session_state.get("setup_v6_postal_code", "").strip(),
        "preferred_store": st.session_state.get("setup_v6_preferred_store", "") or "",
        "appliances": st.session_state.get("setup_v6_appliances", ["microwave"]),
        "goal": st.session_state.get("setup_v6_goal", "").strip(),
        "cuisine_preferences": comma_list(st.session_state.get("setup_v6_cuisines", "")),
        "dietary_restrictions": comma_list(st.session_state.get("setup_v6_restrictions", "")),
        "living_alone_or_partner": st.session_state.get("setup_v6_household", "alone"),
    }


def call_agent(action: str, **values) -> bool:
    """Call the backend while preserving the same session_id for this user session."""
    payload = {"session_id": st.session_state.session_id, "action": action, **values}
    try:
        response = requests.post(AGENT_SERVER_URL, json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            st.session_state.request_error = str(data.get("error"))
            return False
        st.session_state.agent_state = data
        st.session_state.request_error = ""
        return True
    except (requests.RequestException, ValueError) as exc:
        st.session_state.request_error = (
            "We couldn't reach the meal planner. Make sure the agent server is running "
            f"and AGENT_SERVER_URL points to POST /invocations. ({exc})"
        )
        return False


def reset_plan() -> None:
    """Start a truly new agent memory/session."""
    st.session_state.agent_state = {}
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.request_error = ""


def setup_form(compact: bool = False) -> bool:
    """Render the onboarding fields required by initial_generation."""
    if not compact:
        st.caption("All fields below are sent directly to the meal-planning agent.")

    with st.form("plan_settings", border=not compact):
        if not compact:
            st.subheader("Build your plan")

        a, b = st.columns(2)
        with a:
            # The brief confirms 7/14/28. It says a fourth option exists but does not
            # confirm what it is, so we intentionally do not invent it here.
            st.selectbox(
                "Shopping frequency",
                [7, 14, 28],
                format_func=lambda x: f"Every {x} days",
                key="setup_v6_frequency",
            )
            st.number_input(
                "Total grocery budget (SGD)",
                min_value=1.0,
                max_value=5000.0,
                value=55.0,
                step=5.0,
                key="setup_v6_budget",
            )
            st.text_input("Supermarket", value="FairPrice", key="setup_v6_supermarket")
            st.text_input(
                "Singapore postal code",
                max_chars=6,
                placeholder="e.g. 560123",
                key="setup_v6_postal_code",
            )

            labels = [store_label(store) for store in ranked_stores(st.session_state.get("setup_v6_postal_code", ""))]
            current = st.session_state.get("setup_v6_preferred_store")
            if labels:
                if current not in labels:
                    st.session_state["setup_v6_preferred_store"] = labels[0]
                st.selectbox("Preferred store", labels, key="setup_v6_preferred_store")
            else:
                st.text_input(
                    "Preferred store",
                    placeholder="e.g. FairPrice Xtra Ang Mo Kio Hub",
                    key="setup_v6_preferred_store",
                )

            if st.session_state.get("nearest_store_message"):
                st.caption(st.session_state["nearest_store_message"])

        with b:
            st.multiselect(
                "Available appliances",
                ["microwave", "stovetop", "oven", "air fryer", "rice cooker", "blender"],
                default=["microwave"],
                key="setup_v6_appliances",
            )
            st.text_area(
                "Nutritional / meal goal",
                placeholder="e.g. high protein, around 1800 kcal/day, more vegetables and fibre",
                key="setup_v6_goal",
            )
            st.text_input(
                "Cuisine preferences (comma-separated)",
                placeholder="e.g. mediterranean, japanese",
                key="setup_v6_cuisines",
            )
            st.text_input(
                "Dietary restrictions (comma-separated)",
                placeholder="e.g. no pork, no shellfish",
                key="setup_v6_restrictions",
            )
            st.radio(
                "Planning for",
                ["alone", "partner"],
                horizontal=True,
                key="setup_v6_household",
            )
            st.number_input(
                "Days until next shopping trip",
                min_value=0,
                max_value=60,
                value=min(int(st.session_state.get("setup_v6_frequency", 7)), 7),
                step=1,
                key="setup_v6_days_until_next_shopping",
            )

        label = "Update & regenerate plan" if compact else "Make my meal plan"
        return st.form_submit_button(label, type="primary", use_container_width=True)


def meal_day_label(meal: dict, index: int) -> str:
    day = meal.get("day_index")
    return f"Day {day}" if day is not None else f"Meal {index + 1}"


def ingredient_line(item: dict) -> str:
    name = escape(str(item.get("name", "Ingredient")).title())
    quantity = item.get("quantity")
    unit = escape(str(item.get("unit", "")))
    if quantity is None:
        return name
    try:
        quantity_text = f"{float(quantity):g}"
    except (TypeError, ValueError):
        quantity_text = escape(str(quantity))
    return f"{name} <strong>{quantity_text}{(' ' + unit) if unit else ''}</strong>"


def nutrition_totals(meals: list[dict]) -> dict[str, float]:
    totals = defaultdict(float)
    for meal in meals:
        nutrition = meal.get("nutrition") or {}
        for key in ("calories", "protein_g", "carbs_g", "fats_g", "fiber_g"):
            try:
                totals[key] += float(nutrition.get(key) or 0)
            except (TypeError, ValueError):
                pass
    return dict(totals)


def explicit_daily_kcal_target(goal: str) -> float | None:
    """Use only a calorie target the user explicitly typed; do not invent one."""
    match = re.search(r"(\d{3,4})\s*k?cal(?:ories)?\s*(?:/|per\s*)?\s*day", goal, re.I)
    if not match:
        match = re.search(r"(\d{3,4})\s*k?cal", goal, re.I)
    return float(match.group(1)) if match else None


def render_validity_messages(state: dict) -> None:
    if state.get("meals_valid") is False:
        feedback = state.get("meals_feedback") or "The requested meal constraints could not all be satisfied."
        st.warning(f"Meal plan is the closest feasible result: {feedback}")
    elif state.get("meals_feedback"):
        st.info(str(state["meals_feedback"]))

    if state.get("grocery_valid") is False:
        feedback = state.get("grocery_feedback") or "The requested grocery constraints could not all be satisfied."
        st.warning(f"Shopping list is the closest feasible result: {feedback}")
    elif state.get("grocery_feedback"):
        st.info(str(state["grocery_feedback"]))


def render_nutrition(meals: list[dict]) -> None:
    st.subheader("Nutrition progress")
    if not meals:
        st.caption("No meal nutrition returned yet.")
        return

    totals = nutrition_totals(meals)
    cols = st.columns(5)
    metrics = [
        ("Calories", totals.get("calories", 0), "kcal"),
        ("Protein", totals.get("protein_g", 0), "g"),
        ("Carbs", totals.get("carbs_g", 0), "g"),
        ("Fats", totals.get("fats_g", 0), "g"),
        ("Fiber", totals.get("fiber_g", 0), "g"),
    ]
    for col, (label, value, unit) in zip(cols, metrics):
        col.metric(label, f"{value:.0f} {unit}")

    target = explicit_daily_kcal_target(profile().get("goal", ""))
    if target:
        weekly_target = target * len(meals)
        pct = 0 if weekly_target <= 0 else totals.get("calories", 0) / weekly_target
        st.progress(min(max(pct, 0.0), 1.0))
        st.caption(
            f"Calories returned cover about {pct * 100:.0f}% of the explicit {target:.0f} kcal/day "
            f"target you entered across {len(meals)} meal(s)."
        )
    else:
        st.caption(
            "Weekly totals are shown above. The brief does not define numeric nutrition targets, "
            "so the UI does not invent percentages unless your goal text includes an explicit kcal target."
        )


def render_grocery(state: dict) -> None:
    st.subheader("Shopping list")
    grocery = state.get("grocery_list") or []
    if not grocery:
        st.info("The backend did not return a grocery list for this plan.")
        return

    grouped: dict[str, list[dict]] = defaultdict(list)
    total = 0.0
    for item in grocery:
        grouped[str(item.get("category") or "Other")].append(item)
        try:
            total += float(item.get("estimated_cost") or 0)
        except (TypeError, ValueError):
            pass

    budget = profile()["budget"]
    a, b, c = st.columns(3)
    a.metric("Estimated total", f"S${total:.2f}")
    b.metric("Budget", f"S${budget:.2f}")
    c.metric("Difference", f"S${budget - total:.2f}")
    st.progress(min(total / budget, 1.0) if budget > 0 else 0.0)
    st.caption(
        f"Estimated spend is {total / budget * 100:.0f}% of budget." if budget > 0 else "Budget is zero."
    )

    if total > budget:
        st.warning(f"This estimate is S${total - budget:.2f} over your stated budget.")

    for category in sorted(grouped):
        st.markdown(f'<div class="list-kicker">{escape(category)}</div>', unsafe_allow_html=True)
        for item in grouped[category]:
            name = escape(str(item.get("name", "Item")).title())
            quantity = item.get("quantity", "")
            unit = escape(str(item.get("unit", "")))
            already_have = bool(item.get("already_have"))
            try:
                cost = float(item.get("estimated_cost") or 0)
                cost_text = f"S${cost:.2f}"
            except (TypeError, ValueError):
                cost_text = "Price unavailable"

            status = '<span class="already-have">Already have · S$0.00</span>' if already_have else cost_text
            st.markdown(
                f'<div class="grocery-row"><strong>{name}</strong>'
                f'<div class="grocery-meta">{escape(str(quantity))} {unit} · {status}</div></div>',
                unsafe_allow_html=True,
            )


def render_meal_plan(state: dict) -> None:
    meals = state.get("meals") or []
    if not meals:
        st.info("The backend did not return any meals.")
        return

    day_options = ["All days"] + [meal_day_label(meal, i) for i, meal in enumerate(meals)]
    selected_day = st.radio(
        "Show",
        day_options,
        horizontal=True,
        label_visibility="collapsed",
        key="day_filter",
    )

    shown = [
        (i, meal)
        for i, meal in enumerate(meals)
        if selected_day == "All days" or meal_day_label(meal, i) == selected_day
    ]

    for idx, meal in shown:
        nutrition = meal.get("nutrition") or {}
        st.markdown(
            f'<section class="day-card"><div class="day-no">{escape(meal_day_label(meal, idx))}</div>'
            f'<div class="meal-icon">🍽️</div>'
            f'<div class="meal-title">{escape(str(meal.get("name", "Untitled meal")))}</div></section>',
            unsafe_allow_html=True,
        )

        detail, action = st.columns([1.55, 1], gap="large")
        with detail:
            st.markdown("**Ingredients**")
            ingredients = meal.get("ingredients") or []
            if ingredients:
                for item in ingredients:
                    if isinstance(item, dict):
                        st.markdown(
                            f'<div class="ingredient">{ingredient_line(item)}</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f'<div class="ingredient">{escape(str(item))}</div>', unsafe_allow_html=True)
            else:
                st.caption("No ingredients returned.")

            steps = meal.get("steps") or []
            with st.expander("How to cook this"):
                if steps:
                    for n, step in enumerate(steps, 1):
                        st.write(f"{n}. {step}")
                else:
                    st.caption("No cooking steps returned.")

        with action:
            keys = [
                ("calories", "kcal"),
                ("protein_g", "protein g"),
                ("carbs_g", "carbs g"),
                ("fats_g", "fats g"),
            ]
            blocks = "".join(
                f'<div class="nutrient"><b>{escape(str(nutrition.get(key, "—")))}</b><small>{label}</small></div>'
                for key, label in keys
            )
            st.markdown(f'<div class="nutrition-strip">{blocks}</div>', unsafe_allow_html=True)
            st.caption(f"Fiber {nutrition.get('fiber_g', '—')} g")

            with st.form(f"reject_meal_{idx}"):
                reason = st.text_input(
                    "Reject / swap reason",
                    placeholder="e.g. too much garlic, I do not have an oven",
                    key=f"reject_reason_{idx}",
                )
                reject = st.form_submit_button("Reject this meal", use_container_width=True)

            if reject:
                with st.spinner("Asking the agent to fix this meal…"):
                    if call_agent(
                        "reject_meal",
                        rejected_meal_index=idx,
                        rejection_reason_raw=reason.strip(),
                    ):
                        st.rerun()

    learned = state.get("learned_preferences")
    if learned:
        if isinstance(learned, list):
            text = " · ".join(str(x) for x in learned)
        elif isinstance(learned, dict):
            text = " · ".join(f"{k}: {v}" for k, v in learned.items())
        else:
            text = str(learned)
        st.caption(f"The agent remembers: {text}")


state = st.session_state.agent_state
short_id = st.session_state.session_id.split("-")[0].upper()
st.markdown(
    f'<div class="brandbar"><div class="brand">Good Enough to Eat</div>'
    f'<div class="session">Session · {short_id}</div></div>',
    unsafe_allow_html=True,
)

if not state.get("meals"):
    st.markdown('<div class="eyebrow">Agentic meal planning</div>', unsafe_allow_html=True)
    st.title("Waste less. Eat well.")
    st.markdown(
        '<p class="lede"><strong>Plan your meals and groceries together.</strong> '
        'The agent uses your budget, goal, appliances, dietary needs, cuisines and shopping schedule, '
        'and it remembers why you reject a meal within the same session.</p>',
        unsafe_allow_html=True,
    )

    intro, form_col = st.columns([0.72, 1.28], gap="large")
    with intro:
        st.subheader("What you’ll get")
        st.markdown("**A multi-day meal plan**  \nEach meal includes nutrition and full cooking steps.")
        st.markdown("**A priced shopping list**  \nGrouped by category, including items you already have.")
        st.markdown("**Feedback that sticks**  \nReject a meal, say why, and the agent revises it using the same session memory.")
    with form_col:
        submitted = setup_form()

    if submitted:
        p = profile()
        if not p["appliances"]:
            st.error("Select at least one available appliance.")
        elif p["postal_code"] and (len(p["postal_code"]) != 6 or not p["postal_code"].isdigit()):
            st.error("Singapore postal code must be exactly 6 digits, or leave it blank.")
        else:
            with st.spinner("Generating your meal plan…"):
                if call_agent(
                    "initial_generation",
                    **p,
                    days_until_next_shopping=int(
                        st.session_state.get("setup_v6_days_until_next_shopping", p["shopping_frequency_days"])
                    ),
                ):
                    st.rerun()
else:
    top_a, top_b = st.columns([5, 1])
    with top_a:
        st.markdown('<div class="eyebrow">Your plan</div>', unsafe_allow_html=True)
        st.title("Dinner, sorted.")
    with top_b:
        st.write("")
        if st.button("Start over", use_container_width=True):
            reset_plan()
            st.rerun()

    household = "two people" if profile()["living_alone_or_partner"] == "partner" else "one person"
    st.markdown(
        f'<div class="summary"><strong>{len(state.get("meals", []))} meal(s) for {household}</strong><br>'
        f'<span>S${profile()["budget"]:.0f} budget · {escape(profile()["supermarket"] or "No supermarket")} · '
        f'next shop in {int(st.session_state.get("setup_v6_days_until_next_shopping", profile()["shopping_frequency_days"]))} day(s)</span></div>',
        unsafe_allow_html=True,
    )

    render_validity_messages(state)

    with st.expander("Adjust plan settings"):
        changed = setup_form(compact=True)
        if changed:
            p = profile()
            with st.spinner("Regenerating with your updated settings…"):
                if call_agent(
                    "initial_generation",
                    **p,
                    days_until_next_shopping=int(
                        st.session_state.get("setup_v6_days_until_next_shopping", p["shopping_frequency_days"])
                    ),
                ):
                    st.rerun()

    view = st.radio(
        "Choose a view",
        ["Meal plan", "Shopping list", "Nutrition"],
        horizontal=True,
        label_visibility="collapsed",
        key="main_view",
    )

    if view == "Meal plan":
        render_meal_plan(state)
    elif view == "Shopping list":
        render_grocery(state)
    else:
        render_nutrition(state.get("meals") or [])

if st.session_state.request_error:
    st.error(st.session_state.request_error)
"""Warm, resilient Streamlit client for the local meal-planning agent."""
from __future__ import annotations

import uuid
import json
from html import escape
from pathlib import Path
from urllib.parse import quote_plus

import requests
import streamlit as st

from agent.constants import AGENT_SERVER_URL
from agent.tools.fairprice_locator import geocode_postal_code, nearest_store


FAIRPRICE_STORES_PATH = Path(__file__).parent / "agent" / "data" / "fairprice_stores.json"


@st.cache_data
def load_fairprice_stores() -> list[dict]:
    """Load the bundled outlet catalogue so onboarding also works offline."""
    try:
        return json.loads(FAIRPRICE_STORES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def store_label(store: dict) -> str:
    return f'{store["type"]} · {store["name"]} · {store["postal_code"]}'


def ranked_stores(postal_code: str) -> list[dict]:
    """Rank exact/postal-sector matches without implying geographic distance."""
    postal = postal_code.strip()
    return sorted(load_fairprice_stores(), key=lambda store: (
        0 if postal and store["postal_code"] == postal else
        1 if len(postal) >= 2 and store["postal_code"].startswith(postal[:2]) else 2,
        store["type"], store["name"],
    ))


def autoselect_nearest_fairprice() -> None:
    """Resolve a completed postal code and update the outlet dropdown."""
    postal = st.session_state.get("setup_v5_postal_code", "").strip()
    st.session_state["nearest_store_message"] = ""
    if len(postal) != 6 or not postal.isdigit():
        return
    try:
        coordinates = geocode_postal_code(postal)
        if not coordinates:
            raise ValueError("Postal code was not found")
        store, distance = nearest_store(load_fairprice_stores(), *coordinates)
        st.session_state["setup_v5_preferred_store"] = store_label(store)
        st.session_state["nearest_store_message"] = f"Nearest outlet: {store_label(store)} · about {distance:g} km away"
    except (requests.RequestException, ValueError, KeyError, TypeError):
        stores = ranked_stores(postal)
        if stores:
            st.session_state["setup_v5_preferred_store"] = store_label(stores[0])
        st.session_state["nearest_store_message"] = "Live postal lookup was unavailable. We selected the closest postal-sector match; you can change it below."


st.set_page_config(page_title="Good Enough to Eat", page_icon="🍅", layout="wide")
st.session_state.setdefault("session_id", str(uuid.uuid4()))
st.session_state.setdefault("agent_state", {})
st.session_state.setdefault("request_error", "")
UI_STATE_VERSION = 5
if st.session_state.get("ui_state_version") != UI_STATE_VERSION:
    for key in list(st.session_state):
        if key.startswith(("plan_", "setup_")) or key in {"main_view", "nearest_store_message"}:
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
    .lede { color:#4d5c52!important; font-size:1.08rem; max-width:660px; line-height:1.65; }
    .summary { background:var(--forest); color:white; padding:1.2rem 1.35rem; margin:1rem 0 1.8rem; border-left:7px solid var(--coral); }
    .summary strong { color:white; font-size:1.04rem; }
    .summary span { color:#dce7d8; }
    .day-card { background:#fff; border:1px solid #18392b24; border-top:4px solid var(--forest); padding:1.25rem 1.35rem .85rem; margin:.35rem 0 .8rem; box-shadow:0 7px 22px #18392b0b; }
    .day-no { color:var(--coral); font-size:.72rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
    .meal-title { color:var(--forest); font-family:Georgia,serif; font-size:1.55rem; line-height:1.15; margin:.4rem 0 .6rem; }
    .meal-meta { color:#53675b; font-size:.86rem; margin-bottom:.6rem; }
    .ingredient { padding:.25rem 0; border-bottom:1px dotted #b7c7b9; color:#293b31; }
    .nutrition-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:.35rem; margin:.85rem 0 .4rem; }
    .nutrient { background:var(--sage); padding:.55rem .6rem; text-align:center; }
    .nutrient b { display:block; color:var(--forest); font-size:1rem; }
    .nutrient small { color:#506158; font-size:.65rem; text-transform:uppercase; letter-spacing:.06em; }
    .deferred { background:#fffaf1; border:1px dashed #9aac9f; padding:1rem 1.15rem; color:#43554a; }
    .list-kicker { color:var(--coral); font-size:.72rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
    .grocery-row { background:#fff; border-bottom:1px solid #d5ded3; padding:.8rem .9rem; }
    .grocery-row strong { color:var(--forest); }
    .grocery-meta { color:#65746a; font-size:.82rem; margin-top:.18rem; }
    .availability-note { background:var(--sage); border-left:4px solid var(--forest); padding:.9rem 1rem; margin:.7rem 0 1rem; color:#31463a; }
    .availability-note a, .grocery-row a { color:#075f4b!important; font-weight:750; text-decoration-thickness:1px; text-underline-offset:3px; }
    .availability-note a:hover, .grocery-row a:hover { color:#b73e28!important; }
    .grocery-line { display:flex; align-items:center; justify-content:space-between; gap:1rem; }
    .product-link { font-size:.78rem; white-space:nowrap; }
    div.stButton > button, div.stFormSubmitButton > button { background:#fff!important; border:1px solid #718b7c!important; border-radius:2px; color:var(--forest)!important; font-weight:700; min-height:2.7rem; opacity:1!important; }
    div.stButton > button p, div.stButton > button span, div.stFormSubmitButton > button p, div.stFormSubmitButton > button span { color:inherit!important; -webkit-text-fill-color:currentColor!important; opacity:1!important; }
    div.stButton > button:hover, div.stFormSubmitButton > button:hover { background:var(--sage)!important; border-color:var(--forest)!important; color:var(--forest)!important; }
    div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] { background:var(--coral)!important; border-color:var(--coral)!important; color:#fff!important; }
    div.stButton > button[kind="primary"]:hover, div.stFormSubmitButton > button[kind="primary"]:hover { background:#c94931!important; border-color:#c94931!important; color:#fff!important; }
    div[data-testid="stExpander"] { background:white; border:1px solid #18392b24; border-radius:2px; }
    div[data-testid="stMetric"] { background:var(--sage); border:none; padding:.65rem .75rem; }
    div[data-baseweb="select"] > div, [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input { background:#fff!important; border-color:#9cad9e!important; border-radius:2px!important; min-height:2.75rem; color:var(--ink)!important; -webkit-text-fill-color:var(--ink)!important; opacity:1!important; }
    div[data-baseweb="select"] span, div[data-baseweb="select"] input, [data-testid="stNumberInput"] button, [data-testid="stNumberInput"] button svg { color:var(--ink)!important; fill:var(--ink)!important; -webkit-text-fill-color:var(--ink)!important; opacity:1!important; }
    [data-testid="stTextInput"] input::placeholder, div[data-baseweb="select"] input::placeholder { color:#718078!important; -webkit-text-fill-color:#718078!important; opacity:1!important; }
    div[data-baseweb="select"] > div:focus-within, [data-testid="stNumberInput"] input:focus, [data-testid="stTextInput"] input:focus { border-color:var(--coral)!important; box-shadow:0 0 0 2px #e85d3f33!important; }
    span[data-baseweb="tag"] { background:var(--sage)!important; color:var(--forest)!important; border-radius:2px!important; }
    [data-testid="stForm"] { background:#fff; border-color:#b8c6b8!important; border-radius:2px!important; padding:1.2rem!important; }
    @media(max-width:700px){
      [data-testid="stAppViewContainer"] > .main .block-container{padding:1rem 1rem 3rem!important}
      h1{font-size:2.45rem!important;line-height:1.02!important}
      .brandbar{align-items:flex-start;gap:.7rem;margin-bottom:1rem}.session{display:none}
      .lede{font-size:.98rem;line-height:1.5}.nutrition-strip{grid-template-columns:repeat(2,1fr)}
      [data-testid="stHorizontalBlock"]{gap:.75rem!important}
      [data-testid="column"]{min-width:100%!important;width:100%!important;flex:1 1 100%!important}
      [data-testid="stForm"]{padding:.85rem!important}
      div.stButton > button, div.stFormSubmitButton > button{width:100%!important;min-height:3rem}
      .day-card{padding:1rem 1rem .65rem}.meal-title{font-size:1.35rem}.summary{padding:1rem}
      .grocery-line{align-items:flex-start}.product-link{font-size:.75rem}
      span[data-baseweb="tag"]{max-width:100%}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def profile() -> dict:
    """Always attach current controls so checkpointed state cannot go stale."""
    return {
        "shopping_frequency_days": int(st.session_state.get("setup_v5_frequency", 7)),
        "budget": float(st.session_state.get("setup_v5_budget", 100.0)),
        "supermarket": st.session_state.get("setup_v5_supermarket", "FairPrice"),
        "postal_code": st.session_state.get("setup_v5_postal_code", ""),
        "preferred_store": st.session_state.get("setup_v5_preferred_store", ""),
        "appliances": st.session_state.get("setup_v5_appliances", ["stove"]),
        "goal": st.session_state.get("setup_v5_goal", "healthier eating"),
        "cuisine_preferences": st.session_state.get("setup_v5_cuisines", ["asian"]),
        "dietary_restrictions": st.session_state.get("setup_v5_restrictions", []),
        "living_alone_or_partner": st.session_state.get("setup_v5_household", "alone"),
    }


def call_agent(action: str, **values) -> bool:
    payload = {"session_id": st.session_state.session_id, "action": action, **profile(), **values}
    try:
        response = requests.post(AGENT_SERVER_URL, json=payload, timeout=180)
        response.raise_for_status()
        st.session_state.agent_state = response.json()
        st.session_state.request_error = ""
        return True
    except requests.RequestException as exc:
        st.session_state.request_error = f"We couldn't reach the meal planner. Make sure the local agent server is running. ({exc})"
        return False


def reset_plan() -> None:
    st.session_state.agent_state = {}
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.request_error = ""


def setup_form(compact: bool = False) -> bool:
    if st.session_state.get("setup_v5_supermarket", "FairPrice") == "FairPrice":
        st.caption("FairPrice location · enter six digits and we’ll select the nearest physical outlet automatically.")
        st.text_input(
            "Singapore postal code",
            max_chars=6,
            placeholder="e.g. 560123",
            key="setup_v5_postal_code",
            on_change=autoselect_nearest_fairprice,
        )
        stores = ranked_stores(st.session_state.get("setup_v5_postal_code", ""))
        labels = [store_label(store) for store in stores]
        selected = st.session_state.get("setup_v5_preferred_store")
        if selected not in labels:
            st.session_state["setup_v5_preferred_store"] = None
        st.selectbox(
            "Nearest FairPrice outlet",
            labels,
            index=None,
            placeholder="Enter your postal code or choose an outlet",
            key="setup_v5_preferred_store",
        )
        if st.session_state.get("nearest_store_message"):
            st.success(st.session_state["nearest_store_message"])
    with st.form("plan_settings", border=not compact):
        if not compact:
            st.subheader("Build your week")
            st.caption("Tell us how your kitchen works. You can change these later.")
        a, b = st.columns(2)
        with a:
            st.selectbox("Shop every", [3, 7, 14, 28], index=1, format_func=lambda x: f"{x} days", key="setup_v5_frequency")
            st.number_input("Total grocery budget (SGD)", 10.0, 1000.0, 100.0, step=5.0, key="setup_v5_budget")
            selected_supermarket = st.selectbox("Preferred supermarket", ["FairPrice", "Cold Storage", "Default"], key="setup_v5_supermarket")
            st.radio("Planning for", ["alone", "partner"], horizontal=True, key="setup_v5_household")
        with b:
            st.multiselect("Available appliances", ["stove", "oven", "air fryer", "microwave"], default=["stove"], key="setup_v5_appliances")
            st.selectbox("Main goal", ["healthier eating", "high protein", "weight management", "reduce waste"], key="setup_v5_goal")
            st.multiselect("Cuisines you enjoy", ["asian", "chinese-inspired", "japanese-inspired", "mediterranean-inspired", "western"], default=["asian"], key="setup_v5_cuisines")
            st.multiselect("Dietary needs", ["vegetarian", "dairy-free", "gluten-free", "halal"], key="setup_v5_restrictions")
        label = "Update & regenerate plan" if compact else "Make my meal plan"
        return st.form_submit_button(label, type="primary", use_container_width=True)


def render_grocery(state: dict) -> None:
    """Render the immediate shopping view independently of the meal-card stack."""
    st.subheader("Your grocery run")
    if not state.get("grocery_list"):
        st.markdown('<div class="deferred"><strong>Your meal plan is ready, but its shopping preview is missing.</strong><br>Create the estimated list now; you can finalize it after reporting leftovers.</div>', unsafe_allow_html=True)
        if st.button("Create my grocery list", type="primary"):
            with st.spinner("Aggregating ingredients and prices…"):
                if call_agent("preview_grocery"): st.rerun()
        if state.get("grocery_feedback"): st.caption(state["grocery_feedback"])
        return

    list_status = state.get("grocery_list_status", "estimated")
    title = "Final list · ready to shop" if list_status == "final" else "Plan now · estimated"
    st.markdown(f'<div class="list-kicker">{title}</div>', unsafe_allow_html=True)
    total_col, action_col = st.columns([1, 2])
    with total_col:
        st.metric("Priced items total", f"S${state.get('estimated_grocery_cost', 0):.2f}")
        st.caption(f"{len(state['grocery_list'])} ingredients")
    with action_col:
        if list_status == "final":
            st.success("Finalized after reported leftovers. This is the list to take shopping.")
            if st.button("Refresh final list", use_container_width=True):
                with st.spinner("Refreshing quantities from your latest leftovers…"):
                    if call_agent("generate_grocery"): st.rerun()
        else:
            st.caption("Use this estimate to plan. Finalize whenever you are ready to shop; reported leftovers will be subtracted.")
            if st.button("Finalize shopping list", type="primary", use_container_width=True):
                with st.spinner("Subtracting leftovers and finalizing your list…"):
                    if call_agent("generate_grocery"): st.rerun()

    fairprice = profile()["supermarket"] == "FairPrice"
    selected_outlet = profile().get("preferred_store")
    location = profile().get("postal_code") or selected_outlet
    if fairprice:
        locator_url = "https://www.fairprice.com.sg/store-locator"
        if location: locator_url += f"?query={quote_plus(location)}"
        outlet = escape(selected_outlet or "No outlet selected")
        st.markdown(f'<div class="availability-note"><strong>Your FairPrice outlet</strong><br>{outlet}<br><small>Prices are estimates and this app has not checked live shelf stock. Product links search FairPrice’s full online catalogue; set your store there to confirm fulfilment before travelling.</small><br><a href="{locator_url}" target="_blank" rel="noopener">Open FairPrice store locator ↗</a></div>', unsafe_allow_html=True)

    for item in state["grocery_list"]:
        name = escape(str(item["name"]).title())
        quantity = float(item.get("quantity", 0)); cost = item.get("estimated_cost")
        price_copy = f"S${float(cost):.2f} estimated" if cost is not None else "Price estimate unavailable"
        product_link = ""
        if fairprice:
            search_url = f"https://www.fairprice.com.sg/search?query={quote_plus(str(item['name']))}"
            product_link = f'<a class="product-link" href="{search_url}" target="_blank" rel="noopener">Search product ↗</a>'
        st.markdown(f'<div class="grocery-row"><div class="grocery-line"><div><strong>{name}</strong><div class="grocery-meta">Buy {quantity:g}g · {price_copy}</div></div>{product_link}</div></div>', unsafe_allow_html=True)
    if state.get("grocery_feedback"):
        st.warning("No stored price estimate for: " + state["grocery_feedback"] + ". These items are still included above.")


state = st.session_state.agent_state
short_id = st.session_state.session_id.split("-")[0].upper()
st.markdown(f'<div class="brandbar"><div class="brand">Good Enough to Eat</div><div class="session">Local session · {short_id}</div></div>', unsafe_allow_html=True)

if not state.get("meals"):
    st.markdown('<div class="eyebrow">A calmer way to plan dinner</div>', unsafe_allow_html=True)
    st.title("Waste less. Eat well.")
    st.markdown('<p class="lede"><strong>Shop once, with a plan.</strong> A practical meal board for Singapore kitchens—shaped around your budget, dietary needs, appliances and next grocery run.</p>', unsafe_allow_html=True)
    intro, form_col = st.columns([.72, 1.28], gap="large")
    with intro:
        st.subheader("What you’ll get")
        st.markdown("**One clear dinner per day**  \nIngredients measured in grams, with nutrition calculated from stored data—not guessed.")
        st.markdown("**A grocery list from day one**  \nSee an estimated list immediately, then finalize it after leftovers are known.")
        st.markdown("**Feedback that sticks**  \nReject a meal or accept a simple swap; the planner remembers your preference.")
    with form_col:
        submitted = setup_form()
    if submitted:
        with st.spinner("Setting the weekly board…"):
            if call_agent("initial_generation", days_until_next_shopping=profile()["shopping_frequency_days"]):
                st.rerun()
else:
    top_a, top_b = st.columns([5, 1])
    with top_a:
        st.markdown('<div class="eyebrow">Your weekly board</div>', unsafe_allow_html=True)
        st.title("Dinner, sorted.")
    with top_b:
        st.write("")
        if st.button("Start over", use_container_width=True):
            reset_plan(); st.rerun()

    household_label = "two people" if profile()["living_alone_or_partner"] == "partner" else "one person"
    st.markdown(
        f'<div class="summary"><strong>{len(state["meals"])} dinners for {household_label}</strong><br><span>S${profile()["budget"]:.0f} budget · {profile()["supermarket"]} · next shop in {state.get("days_until_next_shopping", profile()["shopping_frequency_days"])} days</span></div>',
        unsafe_allow_html=True,
    )
    with st.expander("Adjust plan settings"):
        changed = setup_form(compact=True)
        if changed:
            with st.spinner("Reworking the board with your new settings…"):
                if call_agent("initial_generation", days_until_next_shopping=profile()["shopping_frequency_days"]): st.rerun()

    view = st.radio(
        "Choose a view",
        ["Groceries", "Meal plan"],
        horizontal=True,
        label_visibility="collapsed",
        key="main_view",
    )
    if view == "Groceries":
        render_grocery(state)
        if st.session_state.request_error: st.error(st.session_state.request_error)
        st.stop()

    meals = state.get("meals", [])
    day_options = ["All days"] + [f"Day {m['day_index']}" for m in meals]
    selected_day = st.radio("Show", day_options, horizontal=True, label_visibility="collapsed")
    shown = meals if selected_day == "All days" else [m for m in meals if f"Day {m['day_index']}" == selected_day]

    for meal in shown:
        idx = meals.index(meal)
        nutrition = meal.get("nutrition") or {}
        appliances = meal.get("appliances_used", meal.get("appliances", []))
        st.markdown(
            f'<section class="day-card"><div class="day-no">Day {meal["day_index"]} · {meal.get("status", "pending")}</div><div class="meal-title">{meal["name"]}</div><div class="meal-meta">{meal.get("estimated_prep_minutes", 30)} min · {" + ".join(appliances) or "no special appliance"} · {meal.get("servings", 1)} serving(s)</div></section>',
            unsafe_allow_html=True,
        )
        detail, action = st.columns([1.6, .9], gap="large")
        with detail:
            st.markdown("**Ingredients**")
            for item in meal.get("ingredients", []):
                st.markdown(f'<div class="ingredient">{item["name"].title()} <strong>{item["quantity"]:g}g</strong></div>', unsafe_allow_html=True)
            if meal.get("steps"):
                with st.expander("Cooking method"):
                    for n, step in enumerate(meal["steps"], 1): st.write(f"{n}. {step}")
        with action:
            keys = [("calories", "kcal"), ("protein_g", "protein"), ("carbs_g", "carbs"), ("fat_g", "fat")]
            blocks = "".join(f'<div class="nutrient"><b>{nutrition.get(k, "—")}</b><small>{label}</small></div>' for k, label in keys)
            st.markdown(f'<div class="nutrition-strip">{blocks}</div>', unsafe_allow_html=True)
            st.caption(f"Fibre {nutrition.get('fibre_g', '—')}g · Sodium {nutrition.get('sodium_mg', '—')}mg · Potassium {nutrition.get('potassium_mg', 'unavailable')}mg")
            missing = meal.get("missing_ingredients", [])
            missing_nutrients = meal.get("missing_nutrients", [])
            if missing: st.warning("Nutrition data missing for: " + ", ".join(missing))
            if missing_nutrients: st.caption("Some nutrient values unavailable: " + ", ".join(missing_nutrients))

        if meal.get("status") != "finished":
            with st.expander("Finish meal or give feedback"):
                with st.form(f"meal_action_{idx}"):
                    mode = st.radio("What would you like to do?", ["Mark as finished", "I don't want this meal"], horizontal=True, key=f"mode_{idx}")
                    if mode == "Mark as finished":
                        leftovers = st.text_input("Leftovers (optional)", placeholder="e.g. broccoli:80, brown rice:120", key=f"leftovers_{idx}")
                        clicked = st.form_submit_button("Finish meal", type="primary")
                        reason = ""
                    else:
                        reason = st.text_input("What isn't working?", placeholder="e.g. I don't like tuna, or I only have a microwave", key=f"reason_{idx}")
                        clicked = st.form_submit_button("Send feedback")
                        leftovers = ""
                if clicked:
                    with st.spinner("Updating your board…"):
                        if mode == "Mark as finished":
                            parsed = []
                            try:
                                parsed = [{"name": p.split(":", 1)[0].strip(), "quantity": float(p.split(":", 1)[1])} for p in leftovers.split(",") if ":" in p]
                            except ValueError:
                                st.error("Use ingredient:grams, for example broccoli:80.")
                            else:
                                if call_agent("report_leftovers", finished_meal_index=idx, current_day=meal["day_index"], new_leftovers=parsed): st.rerun()
                        elif reason.strip():
                            if call_agent("reject_meal", rejected_meal_index=idx, rejection_reason_raw=reason.strip()): st.rerun()
                        else: st.warning("Tell us what isn't working first.")

    suggestion = state.get("substitute_suggestion")
    if suggestion:
        st.subheader("A simpler swap")
        st.info(f"Replace **{suggestion['original_ingredient']}** with **{suggestion['substitute_ingredient']}**. {suggestion['reason']}")
        yes, no, _ = st.columns([1, 1, 2])
        if yes.button("Use this swap", type="primary", use_container_width=True):
            if call_agent("substitute_response", substitute_accepted=True): st.rerun()
        if no.button("Make a new meal", use_container_width=True):
            if call_agent("substitute_response", substitute_accepted=False): st.rerun()

if st.session_state.request_error:
    st.error(st.session_state.request_error)

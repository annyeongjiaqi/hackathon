# Implementation Roadmap (v2)

## Confirmed Scope
- **User:** DINK couple (2 adults, no kids) wanting a healthier lifestyle
- **Problem:** 5 barriers — decision fatigue, ingredient waste, feasibility, cost perception, household diversity (2-person scope)
- **Team:** One main coder + One secondary coder to assist in misc coding tasks + One UI/Streamlit/demo teammate
- **Timeline:** 18 working hours across 3 days
- **Budget:** $20 AWS credits

## Confirmed Stack (matches the taught stack diagram)
| Layer | Choice |
|---|---|
| Interface | Streamlit |
| Deploy & Serve | **Local first** — agent runs as a local `@app.entrypoint` server (`bedrock-agentcore` package), `POST` to `localhost:8080`. Streamlit calls it over HTTP, not as an imported Python function. AgentCore cloud deploy is a Day 3 stretch goal only — code is already compatible. |
| Orchestrate | LangGraph |
| Schema | Pydantic models for structured outputs (meals, grocery list) — model output comes back pre-validated |
| Access | `ChatBedrockConverse` (from `langchain-aws`) |
| Model | Claude Haiku 4.5 (default), Sonnet 4.5 (only if quality demands it) — hardcode the exact model ID as a constant, verify access in your chosen region first |
| Session continuity | LangGraph checkpointer (`InMemorySaver` + `thread_id` = user session id) for short-term "where is this plan right now" state |
| Long-term memory | DynamoDB (on-demand) — permanent, cross-session learned preferences, separate from the checkpointer |

---

## Architecture: This Is 4 Separate Triggered Flows, Not One Pipeline

Walking through the actual app interaction revealed this isn't a single linear run. There are 4 distinct entry points, sharing the same underlying nodes/tools via the LangGraph checkpointer:

| Trigger | Scope | When it fires |
|---|---|---|
| **Initial generation** | Full meal plan (meals only — grocery list deferred, see below) | Once, right after onboarding |
| **Remaining-meals regeneration** | Only meals after today | User marks a meal "finished" + reports leftovers |
| **Single-meal regeneration** | Just one meal | Rejection flow, after substitution also fails |
| **Grocery list generation** | Full list for the upcoming shopping day | Deferred until close to the next shopping day (see below) — NOT regenerated every time a meal is marked finished |

**Why grocery list is deferred:** regenerating the LLM-based grocery list every time a leftover is reported wastes tokens on intermediate versions nobody sees. Instead: leftover ingredients get tracked with a cheap state update (no LLM call, instant), and the actual grocery-generation LLM call only fires once, right before the next shopping day. This is a core agentic-design principle — do as much as possible in cheap deterministic code, and only call the model at the point its reasoning is actually needed.

### Diagram box → implementation mapping

| Diagram box | LLM node or plain tool? |
|---|---|
| Orchestration | Plain Python (LangGraph edges/routing) |
| Creative (meals) | LLM node, Pydantic-structured output |
| Decision-Support (meals) | Plain Python function |
| Creative (grocery list) | LLM node, Pydantic-structured output, deferred trigger |
| Decision-Support (grocery) | Plain Python function |
| Extraction (supermarket) | Plain Python function (lookup against Ingredient DB) |
| Extraction (nutrition count) | Plain Python function (arithmetic over Ingredient DB) |
| Extraction (rejection reason) | LLM node — classify + extract (see below) |
| Personalized | LLM node (for substitute suggestion) + DynamoDB read/write |
| Shelf-life "rule of thumb" | Plain Python pre-processing, passed into Creative(meals) as a constraint |

Only 4 boxes call the LLM: Creative(meals), Creative(grocery), Extraction(rejection reason), Personalized. Every feedback loop needs a hard-coded max-retry counter held in state.

---

## Rejection Handling: Two Branches, Not One

The Extraction (rejection reason) node classifies the user's typed reason into:
- **Preference-fixable** (ingredient dislike, spice level, cuisine) → try a substitute first
- **Constraint-violated** (no equipment, too time-consuming, wrong portion) → skip straight to full meal regeneration; a substitute won't fix a fundamentally wrong recipe

Flow: Extraction classifies + extracts → Personalized always stores the learned reason in DynamoDB (regardless of branch) → if preference-fixable, Personalized proposes one substitute using the **shared substitution tool** → if user rejects the substitute too, or if constraint-violated, run a scoped Creative(meals) call for just that one meal → recalculate nutrition (plain arithmetic, no LLM).

**Shared substitution tool:** the "swap one ingredient, keep the recipe otherwise the same" logic is needed in two places — Creative(grocery) when over budget, and here when a preference issue comes up. Build this once as a shared tool/prompt, don't duplicate it.

---

## Nutrition Calculation: No External Website/API

Bake nutrition (calories, protein, carbs, fats, fiber per 100g) directly into your Ingredient DB as a field, sourced once offline (e.g., from USDA FoodData Central or a Singapore-specific source your team finds — verify what's available) rather than calling a live external tool during the demo. This makes the Nutrition Calculator pure arithmetic: no network call, no flakiness, instant. Skip modeling cooking-method nutrient changes (frying vs. steaming) — not worth the complexity for an 18-hour build.

---

## State Schema (TypedDict, with reducers where needed)

```python
class MealPlanState(TypedDict):
    # Onboarding (fixed for the session)
    shopping_frequency_days: int
    budget: float
    supermarket: str
    appliances: list[str]
    goal: str
    cuisine_preferences: list[str]
    dietary_restrictions: list[str]
    living_alone_or_partner: str

    # Meals
    meals: list[dict]              # each: name, ingredients, day_index, status, nutrition
    meals_valid: bool
    meals_feedback: str
    meals_retry_count: int

    # Grocery (generated on deferred trigger only)
    grocery_list: list[dict]
    grocery_valid: bool
    grocery_feedback: str
    grocery_retry_count: int

    # Leftover tracking (cheap, no LLM)
    leftover_ingredients: list[str]
    days_until_next_shopping: int

    # Rejection handling
    rejection_reason_raw: str
    rejection_category: str        # "preference" | "constraint"

    # Long-term (mirrors DynamoDB, loaded at session start)
    learned_preferences: list[str]

    log: Annotated[list, append]
```

---

## AWS Setup Checklist (Day 1)
1. Bedrock console → Model access → request Claude Haiku 4.5 (and Sonnet 4.5 as backup) in your chosen region
2. IAM → dedicated user (not root) with `bedrock:InvokeModel` + DynamoDB CRUD permissions
3. Access key/secret key → `.env` (never committed); `.env.example` committed instead
4. AWS Budget alert at $15
5. DynamoDB table (on-demand billing), partition key `session_id`, holds `learned_preferences`
6. Avoid: OpenSearch, SageMaker real-time endpoints, NAT Gateway, load balancers, EC2/RDS, Bedrock Provisioned Throughput
7. Hardcode the exact Bedrock model ID as a constant (copy exactly from console, never construct it)

---

## Cowork vs. Claude Code
Cowork = planning/architecture/spec docs. Claude Code (VSCode) = all actual implementation — it has repo access, runs/tests code, is git-aware. Drop this roadmap into `docs/` or `CLAUDE.md` so Claude Code has the context automatically. Don't run both on the same task at once.

---

## 3-Day Plan

### Day 1 (~6h): Setup & Foundations
**You:**
1. Python env + deps: `langgraph`, `langchain-aws`, `boto3`, `python-dotenv`, `streamlit`, `bedrock-agentcore`, `pydantic` (~30 min)
2. AWS setup per checklist above (~1 hour, budget extra time for IAM permission debugging)
3. Test Bedrock connection via `ChatBedrockConverse` (~15 min)
4. DynamoDB table + one write/read test (~30 min)
5. Design State schema (TypedDict) — the shape above (~30 min)
6. Define Pydantic models for meal and grocery-list structured outputs (~30 min)
7. Build deterministic tools: nutrition calculator, supermarket availability lookup, budget validators (meals + grocery), shelf-life rule preprocessor, shared substitution tool (~2.5 hours)

**Friend (in parallel):**
- Document each tool's function signature + docstring
- Chase Group 2 for recipe reference JSON + ingredient DB JSON (with nutrition fields baked in) — build 5-10 placeholder recipes if not ready
- Draft the Creative(meals) system prompt

**End of Day 1 checkpoint:** Bedrock + DynamoDB both work, all deterministic tools are unit-tested with fake data, Pydantic schemas defined.

---

### Day 2 (~7h): Core Agent Build
**You:**
1. Build **Creative(meals)** LangGraph node — `ChatBedrockConverse` + Pydantic structured output (~2 hours)
2. Wire **Decision-Support(meals)** conditional edge + retry bound (~45 min)
3. Build **Extraction(rejection reason)** node — classify preference vs. constraint, extract specifics (~1 hour)
4. Build the rejection subflow: substitution attempt → full single-meal regeneration fallback → nutrition recalc (~1.5 hours)
5. Build the "remaining-meals regeneration" trigger (leftover state update + scoped Creative call) (~1 hour)
6. Set up the LangGraph checkpointer (`InMemorySaver` + `thread_id`) so all these triggers share session state (~45 min)

**Friend (in parallel):**
- Test each node in isolation as you finish it
- Draft Creative(grocery) and Extraction(rejection) system prompts
- Keep formatting Group 2's data

**End of Day 2 checkpoint:** Meal generation, rejection handling, and leftover-triggered regeneration all work end-to-end against a fixed onboarding input.

---

### Day 3 (~5h): Grocery Flow, Personalization, Serving, Polish
**You:**
1. Build **Creative(grocery)** node (deferred trigger, reuses substitution tool for budget overruns) + **Decision-Support(grocery)** edge + **Extraction(supermarket)** tool wiring (~2 hours)
2. Build **Personalized** node — always writes learned reason to DynamoDB, proposes substitute when preference-fixable (~1 hour)
3. Wrap the whole thing with `@app.entrypoint` (`bedrock-agentcore` local runtime), run on `localhost:8080` (~30 min)
4. Hand off to Group 3 — they `POST` onboarding data / meal actions to your local endpoints (~30 min)
5. Buffer: integration testing across all 4 trigger scenarios, bug fixes (~1 hour)

**Friend:**
- End-to-end testing across scenarios
- Demo talking points on "what makes this agentic" (multi-step reasoning, tool use, learning/adaptation)

**Before you stop:**
- Check AWS Billing console — confirm spend well within $20
- If time remains, try the AgentCore cloud deploy as a stretch goal — code is already compatible
- Tear down anything deployed to AWS before finishing

---

## Repo Structure

```
repo/
  agent/
    entrypoint.py             # @app.entrypoint handler, localhost:8080
    state.py                  # TypedDict State
    schemas.py                # Pydantic models for structured outputs
    graph.py                  # LangGraph StateGraph wiring
    checkpointer.py            # InMemorySaver setup
    nodes/
      creative_meals.py
      creative_grocery.py
      extraction_rejection.py
      personalized.py
    tools/
      nutrition_calculator.py
      supermarket_lookup.py
      budget_validator.py
      shelf_life_rules.py
      substitution.py          # shared between grocery + rejection flows
    data/
      recipes_reference.json
      ingredients_db.json      # includes nutrition fields
  streamlit_app.py             # POSTs to localhost:8080
  .env / .env.example
  requirements.txt
  docs/
    implementation_roadmap.md
```

---

## Key Risks to Watch
1. Group 2's data not ready → build with 5-10 placeholder recipes so you're never blocked
2. DynamoDB IAM permission errors → budget real debugging time on Day 1, not Day 3
3. Unbounded retry loops → hard-code max retries in routing logic, every loop
4. Scope creep → resist adding new boxes mid-build
5. Confusing checkpointer state (short-term, per-session) with DynamoDB (long-term, permanent) — keep them conceptually separate in your code

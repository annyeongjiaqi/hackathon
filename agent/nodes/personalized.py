"""Personalized — writes the learned rejection reason to DynamoDB, and the
read side that feeds it back into future generations.

Plain Python, no LLM. Per the roadmap: "Personalized always stores the learned
reason in DynamoDB (regardless of branch)" — this node is the final step on
BOTH rejection-handling paths (substitution applied, or full regeneration), so
every rejection gets remembered whether or not a swap actually fixed it.

Separate store from the LangGraph checkpointer: DynamoDB here is permanent,
cross-session memory (partition key ``session_id``), not the short-term
per-run state the checkpointer holds (roadmap Key Risk #5).

``excluded_ingredients_for_session()`` is the read side ``creative_meals.py``
calls at the start of every generation (initial or scoped regeneration) to
turn past rejections into a HARD EXCLUSION: the ingredient is removed from the
allowed-pantry list the model is told it may use, not just asked nicely to
avoid it (see ``creative_meals._pantry_with_exclusions``). Simpler and more
reliable than a soft steer for a hackathon demo - the tradeoff is that a
one-off "I don't feel like garlic today" complaint permanently bans garlic for
the rest of the session rather than just deprioritizing it. Worth revisiting
(e.g. decay after N days, or a per-rejection "how strict" field) past the
hackathon if genuinely one-off dislikes turn out to be common.

--------------------------------------------------------------------------
Rebuilt from scratch. What was in this file from the teammate's merge did not
survive contact with our current code:
  * ``propose_substitution()`` imported ``suggest_substitution`` from
    ``agent.tools.substitution`` - that function doesn't exist (ours is
    ``suggest_substitute``, a different signature) - the whole module failed
    to import. That responsibility now fully belongs to
    ``agent.nodes.rejection_router.propose_substitution_node`` anyway (it
    already does this, with a confidence threshold and DB-membership check),
    so it wasn't reimplemented here.
  * ``save_preference()`` took one opaque preference string. The task needs
    four distinct fields (session_id, rejection_category,
    rejection_reason_summary, rejection_target_ingredient) written per
    rejection, so it's replaced by ``save_learned_preference()`` below - same
    boto3 Table().update_item() shape she used, restructured to an appended
    list of records instead of a flat string set, plus a read-back helper
    (``load_learned_preferences``) since nothing here ever read the data back.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from agent.constants import AWS_REGION, DYNAMODB_TABLE_NAME
from agent.state import MealPlanState

logger = logging.getLogger(__name__)

_resource = None  # lazy boto3 resource, built once and reused


def _table():
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _resource.Table(DYNAMODB_TABLE_NAME)


def save_learned_preference(
    session_id: str,
    *,
    rejection_category: str,
    rejection_reason_summary: str,
    rejection_target_ingredient: str = "",
    rejection_outcome: str = "",
    meal_name: str = "",
) -> dict | None:
    """Append one learned-preference record to this session's DynamoDB item.

    Uses ``list_append`` so repeated rejections across a session accumulate
    rather than overwrite. Never raises - a DynamoDB outage should not break
    the rejection flow itself; returns None on failure (logged), else the
    record that was written.
    """
    if not session_id:
        logger.warning("personalized: no session_id in state, skipping DynamoDB write")
        return None

    record = {
        "session_id": session_id,
        "rejection_category": rejection_category,
        "rejection_reason_summary": rejection_reason_summary,
        "rejection_target_ingredient": rejection_target_ingredient or "",
        "rejection_outcome": rejection_outcome,
        "meal_name": meal_name,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _table().update_item(
            Key={"session_id": session_id},
            UpdateExpression=(
                "SET learned_preferences = "
                "list_append(if_not_exists(learned_preferences, :empty), :new)"
            ),
            ExpressionAttributeValues={":empty": [], ":new": [record]},
        )
        return record
    except (BotoCoreError, ClientError) as exc:
        logger.warning("personalized: DynamoDB write failed for session %r: %s", session_id, exc)
        return None


def load_learned_preferences(session_id: str) -> list[dict]:
    """Read back every learned-preference record stored for this session."""
    if not session_id:
        return []
    try:
        response = _table().get_item(Key={"session_id": session_id})
    except (BotoCoreError, ClientError) as exc:
        logger.warning("personalized: DynamoDB read failed for session %r: %s", session_id, exc)
        return []
    return response.get("Item", {}).get("learned_preferences", [])


def excluded_ingredients_for_session(session_id: str) -> dict[str, str]:
    """Ingredients Creative(meals) should hard-exclude, learned from this
    session's past rejections: ``{ingredient_name: reason_summary}``.

    Only ``preference_fixable`` rejections with a specific
    ``rejection_target_ingredient`` count. ``constraint_violated`` rejections
    (no oven, too slow, wrong portion size) are deliberately excluded here -
    that information is already captured by the onboarding fields (e.g.
    ``appliances``) that every generation already respects, so replaying it as
    an ingredient "preference" would be redundant at best and actively
    confusing at worst (there's no ingredient to avoid, it was an equipment
    problem).

    Deduped by ingredient: when the same ingredient was rejected more than
    once in this session, the most recent reason wins (returns empty for no
    session_id or no matching history - callers need no special-casing for
    "no history").
    """
    records = [
        r
        for r in load_learned_preferences(session_id)
        if r.get("rejection_category") == "preference_fixable" and r.get("rejection_target_ingredient")
    ]
    records.sort(key=lambda r: r.get("recorded_at", ""))  # chronological, oldest first
    return {
        r["rejection_target_ingredient"]: (r.get("rejection_reason_summary") or "").strip()
        for r in records  # dict comprehension: a later (more recent) entry overwrites an earlier one
    }


def format_excluded_ingredients_note(excluded: dict[str, str]) -> str:
    """One compact prompt line for ``excluded_ingredients_for_session()``'s
    output, or ``""`` when there's nothing to say (no special-casing needed by
    the caller for a fresh session with no rejection history)."""
    if not excluded:
        return ""
    parts = [f"{ingredient} ({reason.rstrip('.')})" if reason else ingredient
             for ingredient, reason in excluded.items()]
    return "User has previously rejected meals for: " + ", ".join(parts) + "."


def _as_summary_line(record: dict) -> str:
    """One human-readable line for state['learned_preferences'] (list[str])."""
    bits = [str(record.get("rejection_reason_summary", "")).rstrip(".")]
    if record.get("rejection_target_ingredient"):
        bits.append(f"(re: {record['rejection_target_ingredient']})")
    if record.get("rejection_outcome"):
        bits.append(f"-> {record['rejection_outcome']}")
    return " ".join(b for b in bits if b)


def personalized_node(state: MealPlanState) -> dict:
    """LangGraph node: persist this rejection's learned reason.

    Reached from both REJECTION_GRAPH endings (substitution applied, or the
    meal was regenerated) - see agent/graph.py.
    """
    session_id = state.get("session_id") or ""
    meals = state.get("meals") or []
    idx = int(state.get("rejected_meal_index") or 0)
    meal_name = meals[idx].get("name", "") if 0 <= idx < len(meals) else ""

    record = save_learned_preference(
        session_id,
        rejection_category=state.get("rejection_category") or "",
        rejection_reason_summary=state.get("rejection_reason_summary") or "",
        rejection_target_ingredient=state.get("rejection_target_ingredient") or "",
        rejection_outcome=state.get("rejection_outcome") or "",
        meal_name=meal_name,
    )

    learned = list(state.get("learned_preferences") or [])
    if record is not None:
        learned.append(_as_summary_line(record))
        log = [f"personalized: wrote learned preference to DynamoDB for session "
               f"{session_id!r} (category={record['rejection_category']})"]
    else:
        log = ["personalized: DynamoDB write skipped or failed - no session_id, or see warning above"]

    return {"learned_preferences": learned, "log": log}


if __name__ == "__main__":  # live DynamoDB smoke test (writes + reads back)
    import uuid

    from dotenv import load_dotenv

    load_dotenv()

    demo_session = f"personalized-smoke-{uuid.uuid4().hex[:8]}"
    demo_state: MealPlanState = {
        "session_id": demo_session,
        "meals": [{"name": "Garlic Chicken with Broccoli", "day_index": 0, "ingredients": []}],
        "rejected_meal_index": 0,
        "rejection_category": "preference_fixable",
        "rejection_reason_summary": "Too much garlic for their taste.",
        "rejection_target_ingredient": "garlic",
        "rejection_outcome": "regenerated",
        "learned_preferences": [],
    }

    out = personalized_node(demo_state)
    print("node output:", out)
    assert out["learned_preferences"], "expected a summary line to be appended"

    fetched = load_learned_preferences(demo_session)
    print(f"read back {len(fetched)} record(s) for session {demo_session!r}:")
    for r in fetched:
        print(" -", r)
    assert len(fetched) == 1
    assert fetched[0]["rejection_target_ingredient"] == "garlic"
    assert fetched[0]["session_id"] == demo_session

    # a second rejection in the same session should APPEND, not overwrite
    second = dict(demo_state)
    second["rejection_category"] = "constraint_violated"
    second["rejection_reason_summary"] = "No oven available."
    second["rejection_target_ingredient"] = ""
    second["rejection_outcome"] = "regenerated"
    personalized_node(second)
    fetched2 = load_learned_preferences(demo_session)
    print(f"after 2nd rejection: {len(fetched2)} record(s)")
    assert len(fetched2) == 2

    print("personalized smoke test OK (live DynamoDB write + read-back verified)")

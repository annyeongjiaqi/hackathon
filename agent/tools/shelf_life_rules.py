"""Shelf-life "rule of thumb" pre-processing (deterministic, no LLM).

Runs *before* Creative(meals). For a plan longer than a week, ingredients bought
on day 1 for a meal cooked on day 20 would have spoiled, so days past the first
week are tagged ``shelf_stable`` and that tag is handed to Creative(meals) as a
per-day constraint. Pure arithmetic — see roadmap "Shelf-Life Rule".

Usage:
    >>> get_freshness_tier(3, 5)
    'fresh'
    >>> get_freshness_tier(20, 28)
    'shelf_stable'
    >>> freshness_tiers_for_plan(10)
    ['fresh', 'fresh', 'fresh', 'fresh', 'fresh', 'fresh', 'fresh', 'fresh', 'shelf_stable', 'shelf_stable']
"""

from __future__ import annotations

from typing import Literal

FreshnessTier = Literal["fresh", "shelf_stable"]

# Days from purchase within which fresh perishables are still safe to cook.
FRESH_WINDOW_DAYS = 7


def get_freshness_tier(day_index: int, total_plan_days: int) -> FreshnessTier:
    """Return ``"fresh"`` or ``"shelf_stable"`` for a given day within the plan.

    Short plans never need this distinction — everything gets used up before
    spoilage is a concern. Longer plans need it only past the first week; the
    first week of even a long plan is still fine fresh.

    Boundary follows the roadmap pseudocode literally: ``day_index <= 7`` is
    still "fresh".
    """
    if total_plan_days <= 7:
        return "fresh"
    return "fresh" if day_index <= FRESH_WINDOW_DAYS else "shelf_stable"


def freshness_tiers_for_plan(total_plan_days: int) -> list[FreshnessTier]:
    """Tier for every day in a plan, indexed 0..total_plan_days-1."""
    return [get_freshness_tier(d, total_plan_days) for d in range(total_plan_days)]


def freshness_constraint_text(total_plan_days: int) -> str:
    """One-line constraint string to drop into the Creative(meals) prompt.

    BUG fixed here (found live-testing the 7-vs-8-day boundary): this used to
    re-derive the shelf_stable range with its own arithmetic
    (``FRESH_WINDOW_DAYS + 1`` to ``total_plan_days - 1``) instead of asking
    ``freshness_tiers_for_plan()``. Because ``get_freshness_tier``'s own
    boundary is inclusive (day_index <= 7 is "fresh"), the actual fresh window
    is 8 days, not 7 - so a `total_plan_days == 8` plan has ZERO shelf_stable
    days, but the old formula still built a range for one, producing the
    nonsensical "Days 8-7: prefer frozen...". Deriving the text from the same
    tier list ``get_freshness_tier`` produces (single source of truth) makes
    this whole class of off-by-one impossible rather than just this instance
    of it.
    """
    tiers = freshness_tiers_for_plan(total_plan_days)
    if "shelf_stable" not in tiers:
        return (
            f"All {total_plan_days} days: any ingredients are fine - every day of this "
            "plan is still within the fresh window from day 1's shopping trip."
        )
    shelf_stable_start = tiers.index("shelf_stable")
    return (
        f"Days 0-{shelf_stable_start - 1}: any ingredients. "
        f"Days {shelf_stable_start}-{total_plan_days - 1}: prefer frozen, canned, "
        "dried, or naturally long-shelf-life ingredients (root veg, grains, legumes) "
        "since everything is purchased on day 1 and these won't spoil."
    )


if __name__ == "__main__":  # smoke test
    assert get_freshness_tier(0, 3) == "fresh"
    assert get_freshness_tier(6, 7) == "fresh"           # 7-day plan: never shelf_stable
    assert get_freshness_tier(7, 28) == "fresh"          # boundary is inclusive
    assert get_freshness_tier(8, 28) == "shelf_stable"
    assert get_freshness_tier(27, 28) == "shelf_stable"
    assert freshness_tiers_for_plan(3) == ["fresh", "fresh", "fresh"]
    tiers = freshness_tiers_for_plan(28)
    assert tiers.count("fresh") == 8 and tiers.count("shelf_stable") == 20

    # regression: total_plan_days == 8 is the edge case that broke the text -
    # every one of its 8 days (day_index 0..7) is "fresh" (inclusive boundary),
    # so there must be no shelf_stable range described at all
    assert freshness_tiers_for_plan(8) == ["fresh"] * 8
    text_8 = freshness_constraint_text(8)
    assert "shelf_stable" not in freshness_tiers_for_plan(8)
    assert "Days 8-7" not in text_8, text_8  # the exact malformed range this used to produce
    assert "prefer frozen" not in text_8, text_8
    assert "All 8 days" in text_8, text_8

    # the real boundary: a 9-day plan is the shortest one with a shelf_stable day
    assert freshness_tiers_for_plan(9) == ["fresh"] * 8 + ["shelf_stable"]
    text_9 = freshness_constraint_text(9)
    assert "Days 0-7: any ingredients" in text_9, text_9
    assert "Days 8-8: prefer frozen" in text_9, text_9

    print("short plan :", freshness_constraint_text(5))
    print("8-day plan :", text_8)
    print("9-day plan :", text_9)
    print("long plan  :", freshness_constraint_text(28))
    print("28-day tiers:", freshness_tiers_for_plan(28))
    print("shelf_life_rules smoke test OK")

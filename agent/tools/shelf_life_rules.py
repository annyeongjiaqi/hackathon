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
    """One-line constraint string to drop into the Creative(meals) prompt."""
    if total_plan_days <= 7:
        return (
            f"All {total_plan_days} days: any ingredients are fine - the whole plan "
            "is eaten within a week of shopping."
        )
    return (
        f"Days 0-{FRESH_WINDOW_DAYS}: any ingredients. "
        f"Days {FRESH_WINDOW_DAYS + 1}-{total_plan_days - 1}: prefer frozen, canned, "
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
    print("short plan :", freshness_constraint_text(5))
    print("long plan  :", freshness_constraint_text(28))
    print("28-day tiers:", freshness_tiers_for_plan(28))
    print("shelf_life_rules smoke test OK")

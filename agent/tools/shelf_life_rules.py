"""Deterministic freshness constraints used before meal generation."""

FRESH_WINDOW_DAYS = 7


def get_freshness_tier(day_index: int, total_plan_days: int) -> str:
    if day_index < 1 or total_plan_days < 1 or day_index > total_plan_days:
        raise ValueError("day_index must be between 1 and total_plan_days")
    if total_plan_days <= FRESH_WINDOW_DAYS:
        return "fresh"
    return "fresh" if day_index <= FRESH_WINDOW_DAYS else "shelf_stable"


def freshness_schedule(total_plan_days: int) -> list[str]:
    return [get_freshness_tier(day, total_plan_days) for day in range(1, total_plan_days + 1)]

"""Classify typed rejection reasons."""
CONSTRAINT_WORDS = {"appliance", "equipment", "oven", "time", "slow", "portion", "serving", "allergy", "diet", "budget"}


def classify_rejection(reason: str) -> dict:
    category = "constraint" if any(word in reason.lower() for word in CONSTRAINT_WORDS) else "preference"
    return {"category": category, "learned_preference": reason.strip()}

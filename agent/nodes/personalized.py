"""Long-term preference persistence and substitution proposals."""
from __future__ import annotations
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from agent.constants import AWS_REGION, DYNAMODB_TABLE_NAME
from agent.tools.substitution import suggest_substitution


def save_preference(session_id: str, preference: str) -> bool:
    try:
        boto3.resource("dynamodb", region_name=AWS_REGION).Table(DYNAMODB_TABLE_NAME).update_item(Key={"session_id": session_id}, UpdateExpression="ADD preferences :p", ExpressionAttributeValues={":p": {preference}})
        return True
    except (BotoCoreError, ClientError, RuntimeError):
        return False


def propose_substitution(meal: dict, restrictions: list[str]) -> dict | None:
    for ingredient in meal.get("ingredients", []):
        replacement = suggest_substitution(ingredient["name"], restrictions)
        if replacement: return {"original_ingredient": ingredient["name"], "substitute_ingredient": replacement, "reason": "Keeps the meal structure while respecting feedback."}
    return None

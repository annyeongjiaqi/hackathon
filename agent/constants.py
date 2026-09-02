"""Central configuration constants for the meal-planner agent.

Everything here is hard-coded on purpose (see roadmap "AWS Setup Checklist" #7):
model IDs are copied verbatim from the Bedrock console, never constructed at runtime.
"""

# ---------------------------------------------------------------------------
# Bedrock model IDs
# ---------------------------------------------------------------------------

# Claude Haiku 4.5 — default model for every LLM node.
# us-east-1 cross-region inference profile. Verified working in Day 1 testing.
BEDROCK_MODEL_ID_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Claude Sonnet 4.5 — backup model, only swap in if Haiku output quality is
# not good enough for a given node. PLACEHOLDER: paste the exact ID from the
# Bedrock console (Model catalog -> Claude Sonnet 4.5 -> cross-region profile)
# once model access is confirmed in us-east-1. Do not construct it by hand.
BEDROCK_MODEL_ID_SONNET = "PLACEHOLDER_SONNET_4_5_MODEL_ID"

# The model every node uses unless it explicitly overrides.
DEFAULT_MODEL_ID = BEDROCK_MODEL_ID_HAIKU

# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

# Region for both Bedrock and DynamoDB. Also present in .env as AWS_REGION /
# AWS_DEFAULT_REGION; kept here too so code paths that don't load .env still agree.
AWS_REGION = "us-east-1"

# DynamoDB table holding long-term, cross-session learned preferences.
# Partition key: session_id. On-demand billing. Separate from the LangGraph
# checkpointer (which is short-term, per-session state only).
DYNAMODB_TABLE_NAME = "user_preferences"

# ---------------------------------------------------------------------------
# Retry limits for the validation feedback loops
# ---------------------------------------------------------------------------
# Every Creative -> Decision-Support loop MUST be bounded (roadmap Key Risk #3).
# When the retry count reaches its max, routing gives up and accepts the last
# attempt rather than looping forever.

MEAL_VALIDATION_MAX_RETRIES = 3      # Creative(meals) <-> Decision-Support(meals)
GROCERY_VALIDATION_MAX_RETRIES = 3   # Creative(grocery) <-> Decision-Support(grocery)

# ---------------------------------------------------------------------------
# Model call defaults
# ---------------------------------------------------------------------------
# A trivial connectivity test needs ~200 tokens. Real meal / grocery generation
# returns a full structured plan (many meals, every ingredient with quantities),
# so it needs a much larger output budget or the JSON gets truncated mid-object.

# Generic defaults for small/cheap calls (e.g. classification).
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1024

# Creative(meals): needs creativity + room for a whole multi-day plan.
MEAL_GENERATION_TEMPERATURE = 0.7
MEAL_GENERATION_MAX_TOKENS = 8192

# Creative(grocery): more deterministic (aggregating a known meal set), still
# a long list with per-item costs.
GROCERY_GENERATION_TEMPERATURE = 0.3
GROCERY_GENERATION_MAX_TOKENS = 4096

# Extraction(rejection reason): pure classify + extract, wants to be repeatable.
REJECTION_EXTRACTION_TEMPERATURE = 0.0
REJECTION_EXTRACTION_MAX_TOKENS = 1024

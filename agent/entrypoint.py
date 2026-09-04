"""AgentCore-compatible local HTTP entrypoint - serves the compiled graphs on
POST http://localhost:8080/invocations (state in, state out).

Thin by design: all the routing logic (which action -> which graph) lives in
``agent.graph.invoke`` so it stays testable in-process, without going through
HTTP, exactly like the rest of the pipeline.
"""
import uuid

from dotenv import load_dotenv

# Must run before agent.graph (-> agent.constants, -> ChatBedrockConverse/boto3
# clients) is imported: unlike the demo scripts under agent/graph.py's
# __main__, nothing else loads .env when this module is run directly as the
# server process, so AWS credentials would otherwise never be picked up.
load_dotenv()

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

from agent.graph import invoke  # noqa: E402

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    # A missing session_id falls back to a fresh one rather than a shared
    # "anonymous" constant, so two callers who both omit it don't collide on
    # the same checkpointer thread_id / DynamoDB partition key.
    session_id = str(payload.pop("session_id", None) or uuid.uuid4())
    return invoke(payload, session_id)


if __name__ == "__main__":
    app.run(port=8080)

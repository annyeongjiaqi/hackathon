"""AgentCore-compatible local HTTP entrypoint (port 8080)."""
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent.graph import invoke

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    session_id = str(payload.pop("session_id", "anonymous"))
    return invoke(payload, session_id)


if __name__ == "__main__":
    app.run(port=8080)

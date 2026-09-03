# Implementation roadmap

This implementation follows the supplied v2 roadmap: Streamlit calls a local AgentCore-compatible server, whose LangGraph graph shares session state through `InMemorySaver`. Creative generation uses `ChatBedrockConverse` and Pydantic schemas; nutrition, shelf-life, pricing and budget checks are deterministic. Grocery generation is deferred, while preferences are persisted to DynamoDB when configured.

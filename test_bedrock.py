from dotenv import load_dotenv
load_dotenv()

from langchain_aws import ChatBedrockConverse

# Paste the exact model ID from Bedrock console -> Model catalog -> Claude Haiku 4.5
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # replace with the real one

model = ChatBedrockConverse(
    model=MODEL_ID,
    region_name="us-east-1",
    temperature=0,
    max_tokens=200,
)

def ask_claude(prompt: str, system: str = "You are a helpful assistant.") -> str:
    messages = [
        ("system", system),
        ("human", prompt),
    ]
    response = model.invoke(messages)
    return response.content

# Example usage
if __name__ == "__main__":
    print(ask_claude("Hello, how are you?"))
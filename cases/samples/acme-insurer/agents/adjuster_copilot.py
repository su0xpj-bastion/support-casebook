"""Adjuster copilot (fictional sample)."""
import anthropic

COPILOT_MODEL = "claude-opus-5-5"
TOOLS = [{"name": "lookup_policy", "description": "Fetch a policy.", "input_schema": {"type": "object", "properties": {}}}]


def ask(client: anthropic.Anthropic, q: str):
    return client.messages.create(
        model=COPILOT_MODEL,
        max_tokens=1024,
        tools=TOOLS,
        tool_choice={"type": "any"},
        thinking={"type": "enabled", "budget_tokens": 4096},
        messages=[{"role": "user", "content": q}],
    )

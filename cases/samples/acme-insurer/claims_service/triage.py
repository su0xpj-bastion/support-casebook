"""Claims triage worker (fictional sample, seeded with migration hazards)."""
import anthropic

MODEL = "claude-sonnet-4-5-20250929"


def triage(client: anthropic.Anthropic, claim_text: str) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        temperature=0.2,
        messages=[{"role": "user", "content": claim_text}],
    )
    return resp.content[0].text

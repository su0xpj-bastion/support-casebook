"""Support chat backend (fictional sample, seeded with hazards)."""
import openai

MODEL = "gpt-4o"


def stream_answer(client: openai.OpenAI, prompt: str) -> str:
    out = []
    for chunk in client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}], stream=True):
        out.append(chunk.choices[0].delta.content or "")
    return "".join(out)


def legacy_thread(client: openai.OpenAI):
    return client.beta.assistants.list()

"""Clean integration (fictional): guarded stream, usage on, billing 429s not retried."""
import openai

MODEL = "gpt-6-luna"
NON_RETRYABLE = {"credit_balance_exhausted", "project_spend_limit_exceeded"}


def answer(client: openai.OpenAI, prompt: str) -> str:
    client = client.with_options(max_retries=0)
    out = []
    with client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}], stream=True,
                                        stream_options={"include_usage": True}) as stream:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                out.append(chunk.choices[0].delta.content)
    return "".join(out)

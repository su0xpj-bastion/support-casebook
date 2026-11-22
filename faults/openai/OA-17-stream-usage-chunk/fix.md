# OA-17 — Stream crashes on chunk.choices[0]; usage missing after interruption

**Retry:** -

Guard `if chunk.choices:` before indexing; read usage from the chunk where `usage` is set; treat a stream without the usage chunk as interrupted (log it, don't bill it as zero).

Sources: https://developers.openai.com/api/docs/guides/streaming-responses, https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

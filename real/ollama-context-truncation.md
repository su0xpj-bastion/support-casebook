# Ollama silently drops the start of long prompts

**Source:** [local-inference-bench](https://github.com/su0xpj-bastion/local-inference-bench) (INC-001), live on an 8 GB M1, Ollama 0.17 and 0.40.16.

## Symptom
"The model ignores the instructions at the top of my long prompt."

## Evidence
| Ollama | Setting | Result on a long prompt |
|---|---|---|
| 0.17 | default | `prompt_tokens` 4,096 — the planted code at the start is lost |
| 0.40 | default | `prompt_tokens` **2,050** — lost (the upgrade made the default stricter) |
| both | `OLLAMA_CONTEXT_LENGTH=16384` | 100–102% of expected tokens, code recalled |

The only trace is the server log: `WARN msg="truncating input prompt" limit=2050 prompt=4466`. A client on the OpenAI-compatible API gets a normal 200 and never sees it.

## Fix / workaround
Set `OLLAMA_CONTEXT_LENGTH` (or the per-request context option) above the longest prompt; alert when `usage.prompt_tokens` is far below the expected count.

## Support lesson
Compare `usage.prompt_tokens` with what was sent. A 200 with fewer input tokens than expected is a data-loss bug, not a model-quality complaint. (This repo's triage eval runs on that same local Ollama, with prompts kept well under the 2,050-token default.)

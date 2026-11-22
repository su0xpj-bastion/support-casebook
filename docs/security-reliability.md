# Security and reliability

Sections split what is **implemented** from **gaps**. Only implemented items are capabilities.

## Secrets

**Implemented**
- **Redaction before storage or display.** Every packet passes through [`redact`](../casebook/redact.py#L36) and [`redact_headers`](../casebook/redact.py#L48). This covers documented key prefixes for both providers, Bearer tokens, JWTs, keys wrapped across lines or JSON-escaped, and secrets inside OpenAI's base64 `x-error-json` header.
- **Secret headers are dropped, not scanned:** `authorization`, `x-api-key`, `api-key`, `openai-api-key`, `cookie`, `set-cookie`.
- **Keys only come from the environment.** Keyed live captures read `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` ([`live._call`](../casebook/live.py#L35)). Free captures use hard-coded fake keys that can't authenticate. `.env` is gitignored.
- **Nothing secret reaches a model.** `triage_llm` redacts before building the prompt; the eval plants 55 keys and counts leaks (0). `test_secrets_never_reach_the_model`.
- **Committed evidence is scanned.** `test_capture_is_well_formed_and_leaks_nothing` fails if any secret pattern survives in a live capture.
- **Generated customer updates are linted for secrets** (`lint_update`).
- **The CLI tests never touch the user's config.** Claude Code and Codex run with throwaway config homes (`tempfile.TemporaryDirectory`), so `~/.claude` and `~/.codex` are never read or written.

**Gaps**
- Redaction is pattern-based. A secret in an unknown format passes through.
- No secret scanning on commits (for example, a pre-commit hook or GitHub secret scanning settings are not configured in this repo).

## Network exposure

**Implemented**
- The mock and all lab servers bind to `127.0.0.1` on a free port.
- The test suite makes no calls to real APIs. Real calls happen only through `python -m casebook live ... --yes` and `tools/doc_links.py --check`. Without `--yes` the live command refuses.
- TLS packs use a CA generated for the run, never a system trust store change.

## Authentication and authorization

This project has no users or login of its own. The identity lab *models* other systems' authentication to reproduce their failures:
- **Real cryptography:** XML signatures (signxml), JWT and JWKS verification (PyJWT), X.509 certificates (cryptography). `test_signature_is_really_checked` confirms a tampered assertion is rejected.
- **Modelled policy:** the service provider, SCIM, OAuth server and WIF rules are written from vendor docs. They are not Anthropic's or OpenAI's systems.

## Input validation

**Implemented**
- Support Packets are validated against [`packet.schema.json`](../casebook/schema/packet.schema.json) in tests (`test_packet_valid`).
- [`validate_client_request_id`](../casebook/packet.py#L63) enforces OpenAI's documented limits.
- Ticket text is parsed defensively: unknown content becomes `None` fields and warnings, never an exception.
- Pack files are checked for required fields and provenance tags (`test_pack_metadata_complete`, `test_pack_provenance_tagged_per_field`).

**Gaps**
- The CLI doesn't validate dossier files beyond YAML parsing; a missing key raises `KeyError`.

## Timeouts, retries, idempotency

| Behaviour | Implementation |
|---|---|
| Retry only what the evidence says is retryable | [`policy._decide`](../casebook/policy.py#L79): `MAX_ATTEMPTS = 3`, retry on verdict `Y` only |
| Jittered exponential backoff, server `retry-after` as a floor | [`policy.backoff`](../casebook/policy.py#L24) |
| Exception order | `RateLimitError` → `APIStatusError` → `APIError`, bare `raise` so callers keep the original traceback |
| Live calls | `max_retries=0`; timeouts 60 s (OpenAI) and 120 s (Anthropic, for the 33 MiB AN-16 body) |
| Triage LLM calls | `max_retries=1`, 120 s timeout, `max_tokens` capped at 200 (an uncapped JSON-mode run once hung for an hour) |
| Node subprocess | 120 s timeout in [`ts_repro.run`](../casebook/ts_repro.py#L21) |
| Retry budget pattern | [`s2_retry_storm.Budget`](../casebook/sims/s2_retry_storm.py#L36): retries limited to 10% of calls in a 1 s window |
| Idempotency | AG-28 shows that deduplicating on the model's `call_id` repeats a side effect after a replayed turn; the fix is a business key checked against the system of record. This is demonstrated, not provided as a library |

SDK default timeouts are 600 s for both providers (OA-16). The packs flag this; the library doesn't change SDK defaults outside its own wrappers.

## Observability

- `MockServer.state` logs every request (path, headers, body, request id) and counts hits per pack; `GET /_casebook/hits` and `/_casebook/requests` expose them.
- The policy wrappers return a `trace` of every attempt with its packet, verdict and sleep.
- Sims write `report.json` and, for SIM-1, a `timeline.jsonl`.
- There is no logging framework, metrics backend or tracing; this is a CLI and test suite.

## Dependencies

**Implemented**
- Python dependencies are pinned to exact versions in [`requirements.txt`](../requirements.txt).
- Node dependencies are locked in [`ts/package-lock.json`](../ts/package-lock.json) and installed with `npm ci`.

**Gaps**
- No automated dependency update or vulnerability scanning (Dependabot or similar) is configured.

## Disaster recovery and data

There is no database or runtime state to recover. Everything is in git: packs, captures, generated runbooks and reports. The only generated files outside git are `drafts/` (gitignored) and the local `.venv/` and `ts/node_modules/`, which are rebuilt from the pinned files.

## Known limitations

- Most packs run against a mock; the mock's message wording is often synthesized (tagged per field).
- Model retirement dates in [`models.py`](../casebook/models.py) are a snapshot; the weekly link check catches moved docs, not changed dates.
- Triage LLM results describe two small local models only.
- Some CLI packs depend on hidden flags (`--managed-settings`) because the real managed-settings path needs sudo; a real managed file may behave differently.

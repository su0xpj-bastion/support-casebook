# Support playbook

Generated from `playbooks/signatures.json`: the same rules `caq triage-log` applies to a log.

| Layer | Owns | Escalate to |
|---|---|---|
| harness | the agent process itself: crashes, won't start | product engineering, as a bug |
| orchestration | the loop around the model: rate limits, timeouts, MCP servers, context length | platform / agent runtime |
| environment | the customer's setup: credentials, proxy and TLS, missing modules, git, docker | customer, with the fix steps |
| model | the run finished but the change is wrong, out of scope, or edits tests | model / evals team, with the task and diff |

## Model API rate limit (429) (`rate-limit`, orchestration)

Matches: `429 Too Many Requests|rate_limit_error|rate limit(ed)? exceeded|RateLimitError`

**First checks**

1. Check whether it is per-minute or a usage-tier / quota limit (the response body says which).
2. Confirm the client retries with backoff and honours retry-after.
3. Lower agent concurrency or move to a higher tier.

**Escalate with:** timestamp and region; request id from the response headers; requests per minute at the time

## Not signed in or invalid credentials (`auth`, environment)

Matches: `401 Unauthorized|invalid[_ ]api[_ ]key|authentication_error|not logged in|please (log|sign) in|Invalid bearer token`

**First checks**

1. Check which credential source the CLI resolved (env var vs saved login).
2. An env var with an old key shadows a newer login.
3. Regenerate the key if it was rotated.

**Escalate with:** CLI version; which credential source is active (never the key itself); account / org id

## Corporate proxy or TLS interception (`proxy-tls`, environment)

Matches: `CERTIFICATE_VERIFY_FAILED|self[- ]signed certificate|unable to get local issuer certificate|ECONNRESET|tunneling socket could not be established|407 Proxy Authentication`

**First checks**

1. Check HTTPS_PROXY / NO_PROXY.
2. Point the runtime at the corporate CA bundle (SSL_CERT_FILE / NODE_EXTRA_CA_CERTS / REQUESTS_CA_BUNDLE).
3. Test the endpoint with curl -v from the same shell.

**Escalate with:** proxy product if known; curl -v output with tokens removed; OS and runtime versions

## Missing dependency in the workspace (`missing-module`, environment)

Matches: `ModuleNotFoundError: No module named|Cannot find module|command not found: (python|node|npm|pytest)`

**First checks**

1. Install the project's dependencies in the environment the agent runs in (not only your shell).
2. Check the agent uses the same interpreter / virtualenv.
3. Add the setup step to the environment blueprint so it persists.

**Escalate with:** the missing module; how the environment is provisioned; python / node version

## Git push or clone refused (`git-auth`, environment)

Matches: `Permission denied \(publickey\)|Authentication failed for 'https://|remote: Repository not found|could not read Username`

**First checks**

1. Check the integration has access to this repository.
2. SSH key or token missing in the agent's environment.
3. Branch protection can also refuse a push.

**Escalate with:** repo and branch; git remote -v; git host (GitHub / GitLab / Bitbucket)

## Container runtime unavailable (`docker`, environment)

Matches: `Cannot connect to the Docker daemon|docker: command not found|permission denied while trying to connect to the Docker daemon`

**First checks**

1. Start the Docker daemon or add the user to the docker group.
2. In CI, use a runner with Docker available.

**Escalate with:** OS; docker version; how the agent was launched

## MCP server failed to start or connect (`mcp-start`, orchestration)

Matches: `MCP server .* (failed|exited)|Failed to connect to MCP|mcp.*ENOENT|Connection closed.*mcp`

**First checks**

1. Run the server command by hand in the same directory.
2. Check the command path and env vars in the MCP config.
3. For remote servers, check the URL and the bearer token.

**Escalate with:** the MCP config entry (secrets removed); server stderr; client and server versions

## Context window exceeded (`context-length`, orchestration)

Matches: `context[_ ]length[_ ]exceeded|prompt is too long|maximum context length|input length .* exceeds`

**First checks**

1. Large files or logs were read whole; narrow the reads.
2. Enable compaction or start a fresh session with a summary.
3. Exclude build output and vendored code from the workspace.

**Escalate with:** approximate repo size; the step where it happened

## Agent ran past its time budget (`timeout`, orchestration)

Matches: `timed out|TimeoutExpired|deadline exceeded|exit code 124`

**First checks**

1. Check whether it was stuck in a loop (same command repeated) or slow tests.
2. Raise the budget only after the loop cause is ruled out.

**Escalate with:** session id; last 50 lines of the log

## Agent process crashed (`agent-crash`, harness)

Matches: `Traceback \(most recent call last\)[\s\S]*agent/|Segmentation fault|panicked at|exit code 134|FATAL ERROR`

**First checks**

1. Collect the full stack trace and CLI version.
2. Retry once to see whether it reproduces.
3. This is a product bug: escalate, don't work around.

**Escalate with:** full stack trace; CLI version and OS; exact command; whether it reproduces

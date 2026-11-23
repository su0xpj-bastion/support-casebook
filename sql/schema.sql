-- Evidence tables loaded by casebook.logdb. One database per load; nothing here is hand-edited.
CREATE TABLE packs (
  id TEXT PRIMARY KEY, provider TEXT, surface TEXT, kind TEXT, title TEXT,
  status INTEGER,            -- HTTP status the case returns (NULL for client-side failures)
  error_type TEXT,           -- error.type
  error_code TEXT,           -- error.code (OpenAI) or error.details.error_code (Anthropic)
  verdict TEXT, retry TEXT,  -- expected class and Y / N / C
  owner TEXT, provenance TEXT
);
CREATE TABLE tickets (
  id TEXT PRIMARY KEY, provider TEXT, family TEXT, source TEXT, evidence_level TEXT,
  gold TEXT, gold_retry TEXT, pack TEXT, planted_secret INTEGER, abstain_ok INTEGER
);
CREATE TABLE predictions (
  model TEXT, mode TEXT, ticket TEXT REFERENCES tickets(id), label TEXT, retry TEXT, basis TEXT,
  unsupported_quotes INTEGER, leaked_secret INTEGER, llm_error INTEGER
);
CREATE TABLE live_captures (
  pack TEXT REFERENCES packs(id), captured_utc TEXT, status INTEGER, x_should_retry TEXT,
  exception TEXT, verdict TEXT, fields_matched INTEGER, fields_total INTEGER
);
CREATE TABLE repro_runs (
  run TEXT, pack TEXT REFERENCES packs(id), mode TEXT,  -- mode: sdk-default | policy
  requests_sent INTEGER, exception TEXT, verdict TEXT, retry TEXT, provider_request_id TEXT
);
CREATE TABLE requests (
  run TEXT, pack TEXT, mode TEXT, seq INTEGER, path TEXT,
  request_id TEXT,           -- the id the server returned for this attempt
  retry_count INTEGER,       -- x-stainless-retry-count sent by the SDK
  client_request_id TEXT     -- X-Client-Request-Id, when the caller set one
);
CREATE INDEX idx_pred ON predictions(model, mode);
CREATE INDEX idx_req ON requests(pack, mode);

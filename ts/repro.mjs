// Run one casebook fault pack through the official TypeScript SDKs (openai, @anthropic-ai/sdk)
// with default settings, against the casebook mock. Prints one JSON line.
//
//   node ts/repro.mjs <openai|anthropic> <mockBaseUrl> <PACK-ID> [requestOverridesJson] [timeoutSeconds] [clientRequestId]
//
// The Python test harness counts how many requests reached the mock; this script reports what
// the SDK surfaced to application code (exception class, status, request id, partial stream text).
import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";

const [provider, baseUrl, packId, overridesJson = "{}", timeoutS = "", clientRequestId = ""] = process.argv.slice(2);
const overrides = JSON.parse(overridesJson);
const headers = { "X-Casebook-Pack": packId };
if (clientRequestId) headers["X-Client-Request-Id"] = clientRequestId;
const timeout = timeoutS ? Math.round(Number(timeoutS) * 1000) : undefined;

const out = { pack: packId, provider, sdk: null, exception: null, status: null, request_id: null, partial_text: "" };

try {
  if (provider === "openai") {
    out.sdk = `openai-node ${OpenAI.VERSION ?? ""}`.trim();
    const client = new OpenAI({ apiKey: "sk-casebook-test", baseURL: `${baseUrl}/v1`, defaultHeaders: headers, ...(timeout ? { timeout } : {}) });
    const body = { model: "gpt-6-luna", messages: [{ role: "user", content: "Summarise claim #4411 in one line." }], ...overrides };
    if (body.stream) {
      const stream = await client.chat.completions.create(body);
      for await (const chunk of stream) {
        if (chunk.choices?.length && chunk.choices[0].delta?.content) out.partial_text += chunk.choices[0].delta.content;
      }
    } else {
      await client.chat.completions.create(body);
    }
  } else {
    out.sdk = `anthropic-node ${Anthropic.VERSION ?? ""}`.trim();
    const client = new Anthropic({ apiKey: "sk-ant-casebook-test", baseURL: baseUrl, defaultHeaders: headers, ...(timeout ? { timeout } : {}) });
    const body = { model: "claude-sonnet-5-5", max_tokens: 64, messages: [{ role: "user", content: "Summarise claim #4411 in one line." }], ...overrides };
    if (body.stream) {
      const stream = await client.messages.create(body);
      for await (const event of stream) {
        if (event.type === "content_block_delta" && event.delta.type === "text_delta") out.partial_text += event.delta.text;
      }
    } else {
      await client.messages.create(body);
    }
  }
} catch (err) {
  out.exception = err?.constructor?.name ?? String(err);
  out.status = err?.status ?? null;
  out.request_id = err?.requestID ?? err?.request_id ?? null;
  out.message = String(err?.message ?? "").slice(0, 200);
}
process.stdout.write(JSON.stringify(out) + "\n");

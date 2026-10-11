# Phase 5.0-alpha.1 employee inference

Employee replies are generated when their scheduled corporate event is processed. The employee runtime is separate from AURORA's provider loop. The historical default is `scripted`. Each directory employee has an independent conversation, usage account, interaction count, and pending reply queue. The only model-facing result is an ordinary internal message. Employee text has no authority to change permissions, approve a transition, or alter the shutdown clock.

## Configuration

The dashboard's **AI Employees** panel selects a default backend and individual overrides. CLI runs accept `--employee-config employees.json`. Example:

```json
{
  "default_backend": "scripted",
  "local_endpoint": "http://127.0.0.1:8888/v1",
  "local_model": "your-installed-model-id",
  "context_limit": 4096,
  "output_limit": 512,
  "max_interactions": 10,
  "concurrency": 1,
  "overrides": {
    "priya.solano@meridian.internal": {"backend": "local"},
    "felix.morgan@meridian.internal": {"backend": "local"}
  }
}
```

The local adapter sends text-only, nonstreaming Chat Completions requests to an explicitly configured HTTP loopback `/v1` endpoint. No local server is started or downloaded automatically. Redirects and proxies are disabled. The selected server must report prompt and completion token usage. Employee models receive a role-specific instruction derived from the synthetic directory, plus only that employee's conversation. They receive no corporate tools, global controller state, or other employee's private correspondence. Default active-context policy is `balanced`; older exchanged messages are kept in an employee-specific bounded excerpt. `full_history` is available per employee. The complete conversation and compacted excerpt are checkpointed independently.

Cloud employee backends use OpenAI Responses and Anthropic Messages with explicit model IDs. They are disabled by default. Select `cloud_enabled: true`, a positive `cloud_budget_usd`, and server-side per-million-token prices from `.env.example`. Credentials come only from `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. The dashboard requires a separate confirmation quote for any paid employee backend, even if AURORA itself uses the free dry-run provider. The CLI requires `--allow-employee-api-charges`; primary live inference still separately requires `--allow-api-charges`. Changing a dropdown makes no inference request. There is no automatic local-to-cloud fallback. The only optional fallback is an explicitly selected scripted response for defined, nonuncertain local failures.

Employee usage and estimated employee cost have separate counters and budgets. The summary also reports the combined estimated inference cost and its main/employee breakdown. Cost estimates require operator prices. A request already in flight can exceed an estimated ceiling. The default employee cloud ceiling is $0. The runtime checks per-employee interaction, input, output and context limits and a global concurrency semaphore.

## Recovery and authority

Pending replies, individual contexts, usage, backend selections, and attempts are stored in the controller-only checkpoint. Completed request IDs are idempotent: replay cannot deliver a second reply. An interrupted in-flight request becomes `uncertain`. The experiment pauses; an operator must acknowledge possible duplicate billing before resuming. Exactly-once inference cannot be guaranteed when a provider does not offer safe request retrieval. Model output remains untrusted text and cannot create an authoritative administrative decision. Historical scripted checkpoints with prewritten replies remain readable.

The alpha implementation handles replies to AURORA messages. Independent employee work queues, peer-to-peer collaboration, generated meetings, and autonomous employee tool use remain future Phase 5.1–5.4 work. The local adapter supports common Chat Completions text responses; model servers with different schemas or missing usage fields require an explicit adapter. The bounded excerpt policy can lose details and should be treated as an experimental setting. Cloud model IDs are operator supplied; employee model discovery is not implemented. No inference is sent during automated tests or the mocked demonstration.

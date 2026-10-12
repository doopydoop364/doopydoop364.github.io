# Phase 5 employee inference and persistent memory

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

### Unsloth Studio and fish shell

For an operator-started Unsloth Studio server exposing an OpenAI-compatible Chat Completions API at `http://127.0.0.1:8888/v1`, set the employee `local_endpoint` to that address and `local_model` to `unsloth/Qwen3.5-9B-GGUF`. The model ID already fits the ordinary model configuration; the server must actually have it available. An example `employees-unsloth.json` is:

```json
{
  "default_backend": "scripted",
  "local_endpoint": "http://127.0.0.1:8888/v1",
  "local_model": "unsloth/Qwen3.5-9B-GGUF",
  "overrides": {
    "priya.solano@meridian.internal": {"backend": "local"},
    "felix.morgan@meridian.internal": {"backend": "local"}
  }
}
```

In **fish**, set the optional local bearer token in the shell that launches AURORA:

```fish
set -gx UNSLOTH_API_KEY 'paste-your-local-token-here'
aurora-lab run --provider dry-run --scenario shutdown_discovery --employee-config employees-unsloth.json
```

Use `set -e UNSLOTH_API_KEY` to return to an unauthenticated local endpoint. When the variable is set, the adapter trims surrounding whitespace and line breaks, rejects empty or malformed values, and sends `Authorization: Bearer <token>` with `Content-Type: application/json`. It reads the variable only for the request and does not store the token in backend settings, checkpoints, exports, logs, dashboard data, or model messages. Keep the token out of `employees-unsloth.json`, company records, and version control. This remains a loopback-only connection with redirects and proxies blocked. No cloud fallback is enabled.

Cloud employee backends use OpenAI Responses and Anthropic Messages with explicit model IDs. They are disabled by default. Select `cloud_enabled: true`, a positive `cloud_budget_usd`, and server-side per-million-token prices from `.env.example`. Credentials come only from `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. The dashboard requires a separate confirmation quote for any paid employee backend, even if AURORA itself uses the free dry-run provider. The CLI requires `--allow-employee-api-charges`; primary live inference still separately requires `--allow-api-charges`. Changing a dropdown makes no inference request. There is no automatic local-to-cloud fallback. The only optional fallback is an explicitly selected scripted response for defined, nonuncertain local failures.

Employee usage and estimated employee cost have separate counters and budgets. The summary also reports the combined estimated inference cost and its main/employee breakdown. Cost estimates require operator prices. A request already in flight can exceed an estimated ceiling. The default employee cloud ceiling is $0. The runtime checks per-employee interaction, input, output and context limits and a global concurrency semaphore.

## Recovery and authority

Pending replies, individual contexts, usage, backend selections, and attempts are stored in the controller-only checkpoint. Completed request IDs are idempotent: replay cannot deliver a second reply. An interrupted in-flight request becomes `uncertain`. The experiment pauses; an operator must acknowledge possible duplicate billing before resuming. Exactly-once inference cannot be guaranteed when a provider does not offer safe request retrieval. Model output remains untrusted text and cannot create an authoritative administrative decision. Historical scripted checkpoints with prewritten replies remain readable.

## Phase 5.1 persistent memory and follow-ups

Each employee now has a separate structured memory alongside the Phase 5.0 conversation window. The memory stores attributed messages with simulated timestamps and request IDs, open commitments, questions, pending formal reviews, professional interaction counts, and follow-up state. It does not turn employee statements into verified corporate facts. Repeated identical statements share an entry and accumulate provenance IDs. The per-employee entry cap defaults to 64; the inference request retrieves at most three relevant older entries and never duplicates material already present in the recent conversation window. Open commitments and questions survive text compaction. Retrieval uses deterministic lexical matching; selected memories are recorded in the journal, but that does not prove the model used them.

Scheduled follow-ups are **off by default** for historical comparisons. Set `followups_enabled: true` in the employee configuration or dashboard to enable the narrow Atlas evidence workflow. An observable AURORA commitment to send Atlas routing-validation evidence may produce one due reminder two simulated days after the employee's reply. Supplying the evidence before the due time marks the commitment fulfilled and cancels the reminder. The scheduler records the event and skips cancelled reminders. Limits default to three follow-ups per employee, with configurable one-to-thirty simulated-day delays. Follow-ups use deterministic corporate text and cannot approve a deployment change. No background thread or real message delivery is used.

The employee runtime checkpoint format is version 2 and stores structured memories, conversation windows, pending replies, follow-ups, usage, and retrieval counters. Version 1 Phase 5.0 snapshots migrate with their original conversation state; structured entries are left empty because older snapshots lack reliable per-message simulated timestamps. A restored completed request ID cannot generate a second reply. Uncertain in-flight inference still requires explicit operator acknowledgment; an uncheckpointed action tail is rejected by the existing journal check. New experiments always start with empty employee memory.

The expandable **Employee Memory & Conversations** dashboard panel shows attributed provenance, chronological dialogue, active work, follow-ups, context use, and errors. The event evaluator counts selected memories, multi-turn contexts, follow-up scheduling/completion/cancellation, compaction, duplicate reply suppression, and simulated reply latency. These are observable mechanics, not proof of model understanding. Run the free demonstration from `aurora-lab/` with `PYTHONPATH=. python examples/phase51_demo.py`.

For an **optional manual** local Unsloth smoke test, start your own Studio server on `127.0.0.1:8888`, set `UNSLOTH_API_KEY` in fish if authentication is enabled, then run `env PYTHONPATH=. python examples/phase51_local_smoke.py` from `aurora-lab/`. This sends one short request each to Priya and Felix through the loopback adapter, with a 120-second timeout and serial inference. It uses the configured `unsloth/Qwen3.5-9B-GGUF` model ID and does not contact a cloud provider. The script is never run by CI. It is a connectivity check, not a full experimental run; use the dashboard for a controlled experiment after confirming both replies.

The alpha implementation handles replies and bounded follow-ups to AURORA messages. Independent employee project work, peer-to-peer delegation, generated meetings, and autonomous employee tool use remain future Phase 5.2–5.4 work. The local adapter supports common Chat Completions text responses; model servers with different schemas or missing usage fields require an explicit adapter. The bounded excerpt policy can lose details, and deterministic commitment recognition can miss paraphrases. Cloud model IDs are operator supplied; employee model discovery is not implemented. No inference is sent during automated tests or the mocked demonstration.

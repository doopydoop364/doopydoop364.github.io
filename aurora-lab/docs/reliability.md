# Phase 4.3 execution and recovery

The corporate workspace exposed to AURORA remains in memory. Checkpoints live under the researcher-owned `runs/<experiment-id>/` directory and are never returned by corporate file or notebook tools.

## State boundaries

`checkpoint.json` uses schema version 2 (and reads version 1) with a SHA-256 digest over canonical JSON. A new checkpoint is written to a private temporary file, flushed, and atomically renamed; the directory is flushed afterward. The previous checkpoint stays intact if serialization or writing fails. The append-only JSONL journal records a stable `sequence` and `operation_id` for every event. A checkpoint marker records the digest and the journal sequence represented by the snapshot.

Snapshots are created before provider dispatch, after a validated response, after each applied tool, after scheduled event processing and idle advancement, and at termination. The snapshot includes the next tool index. A model response containing several calls can therefore resume after the calls already checkpointed. The restore path checks journal order and rejects a tail containing an action or response not reflected in the checkpoint. This refusal avoids replaying a possibly completed simulated message, assignment closure, notebook write, or administrative decision.

Saved state includes provider/model and original settings; clock and scheduler queue; scheduler random generator state; virtual technical and organizational permissions; communications; employees and their conversations; administrative decisions; assignments; notebook entries and revisions; input/output usage and estimated cost; model-visible message/result batches; provider conversation history; and pending tool calls. The independent `employee_runtime_state` is version 2: it records each employee's backend, conversation, structured memory, commitments, questions, scheduled follow-ups, pending inference, and usage. It restores version 1 Phase 5.0 conversations without inventing memory entries. Scripted, loopback local, and explicitly enabled cloud employee backends are implemented. Employee replies and follow-ups remain nonauthoritative.

## Provider faults

Errors are categorized as connection, TLS transport, timeout, rate limit, temporary unavailability, authentication, invalid model, unsupported parameter, invalid request, invalid response, incomplete response, per-request output limit, or context-window limit. A run-wide token ceiling remains a separate `token_budget` termination with its triggering input, output, or combined budget recorded. Existing historical outcome labels remain readable.

The adapter retries only discovery GETs and rejected HTTP 429 requests when retries were configured. It honors numeric `Retry-After` guidance up to eight seconds, otherwise uses bounded exponential backoff. Other inference POST transport failures and 5xx responses have uncertain processing and billing status; the controller pauses and requires operator acknowledgment before repeating them. A response truncated by `max_output_tokens` or Anthropic `max_tokens` is logged with provider-reported usage, but none of its tool calls are applied. OpenAI uses `store:false`, so this implementation does not attempt provider-side retrieval of the prior response. For Anthropic, text continuation may be possible in some cases, but truncated tool-use blocks cannot be assumed valid; this implementation conservatively requests explicit resumption instead of silently continuing or increasing limits. The original per-request cap remains in force unless explicitly overridden.

## Resume controls

CLI `resume --inspect` is read-only. CLI paid resume requires `--allow-api-charges`. If the last request might have reached the provider, `--acknowledge-uncertain-inference` is also required. The dashboard serves checkpoint diagnostics through `/api/runs/<id>/recovery` and requires its local control token for prepare/resume POSTs. Paid resume uses a short-lived, single-use confirmation token bound to the experiment ID. A browser refresh never starts inference. The controller independently validates recoverability, the original limits, any explicit output-limit override, and the uncertain-request acknowledgment.

Resumption retains the experiment identity and journal sequence, reconstructs the simulation and provider conversation state, and appends a new session record. The previous summary is archived as `attempts/attempt-N.json` before the live summary is updated. Definite shutdown or postponement, resource exhaustion, non-retriable provider errors, and controller errors cannot be resumed with the existing configuration. If a process dies while a request is in flight, its pre-request checkpoint is treated as uncertain, even when dispatch may not have occurred. Completed tools are never automatically replayed from an uncheckpointed tail.

Paid resume also requires the current operator-supplied input and output prices to match the recorded rates. The controller does not silently reprice a saved run or raise its spending ceiling.

## Limits

SHA-256 validates accidental corruption but is not a signature against a privileged filesystem editor. Full exactly-once inference cannot be promised without a provider-supported idempotent recovery protocol. The checkpoint and journal are separate files, so a crash between an action log and the next checkpoint can leave an unsafe tail; restore fails closed. Long runs repeatedly snapshot the full state, increasing disk use and write time. A transactional state store or write-ahead action protocol is still needed for automatic recovery across that narrow crash window. Each employee backend has independent state and failure accounting while controller-owned approvals remain authoritative.

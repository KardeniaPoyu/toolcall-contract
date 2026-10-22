# Semantic contract v1

The oracle audits observed evidence; it never repairs or deduplicates a trace. One trace describes one
scenario/request lifecycle. Event indexes in findings are zero-based.

## Trace envelope

```json
{
  "schema_version": 1,
  "target": "my-sdk",
  "evidence": "external",
  "scenario": {
    "name": "example", "mode": "normal", "cut_after": 1,
    "calls": [{"name": "record", "arguments": {"x": 1}}]
  },
  "events": [
    {"kind": "attempt_start", "attempt": 0, "data": "primary"},
    {"kind": "tool_start", "attempt": 0, "slot": "0", "call_id": "call_a", "name": "record"},
    {"kind": "args", "attempt": 0, "slot": "0", "data": "{\"x\":1}"},
    {"kind": "tool_end", "attempt": 0, "slot": "0"},
    {"kind": "finish", "attempt": 0, "data": "tool_calls"},
    {"kind": "stream_end", "attempt": 0},
    {"kind": "close", "attempt": 0},
    {"kind": "attempt_end", "attempt": 0, "data": "success"},
    {"kind": "execute", "attempt": 0, "slot": "0", "call_id": "call_a", "name": "record", "data": {"x": 1}}
  ]
}
```

Attempts start at zero, are sequential and cannot overlap. Each has fresh slot/ID namespaces.
Call IDs are not durable business idempotency keys; reuse between an interrupted attempt and retry
is legal. A slot identifies one streamed call within one attempt.

## Events

| Kind | Evidence |
|---|---|
| attempt_start | `data`: target name |
| tool_start | `slot`, nonempty `call_id`, nonempty `name` |
| args | `slot`, `data`: incremental text appended literally |
| tool_end | `slot`, optional `data`: final JSON string snapshot compared with deltas |
| finish | `data`: `tool_calls`, `tool_use`, or `completed` for accepted calls |
| stream_end | Protocol terminal; SDK Chat instead annotates weaker `sdk_eof` |
| cancel | Consumer requested cancellation |
| fault | `injected_disconnect` for planned interruption; other faults fail |
| close | Source close successfully completed |
| attempt_end | `success`, `cancelled`, `interrupted`, or `failed` |
| execute | Completed identity plus parsed arguments, after successful closed attempt |
| violation | Adapter-observed protocol error, with sanitized static text |

Normal expects one success. Cancel expects one cancelled attempt with no execution. Retry/fallback
expect an intentional interruption followed by success. Retry preserves the target name; fallback
changes it. New attempts after cancellation or a successful commit fail.

`cut_after` counts nonempty argument deltas. Built-in value `10000` is reserved for cutting at the
first `tool_end`, before response commit. If a stream ends before a required cut point, it fails the
scenario lifecycle; returning no call cannot produce a false pass.

## Arguments and multiplicity

Expected calls form a multiset of `(name, canonical JSON arguments)`. Object key and call arrival
order do not matter. Array order, types, values, extra/missing fields and multiplicity do. JSON numeric
serialization (`1` vs `1.0`) and Unicode normalization are preserved. Identical intentional calls are
legal only when the contract expects them.

Arguments must be strict JSON objects. Duplicate keys, non-finite numbers, partial JSON, scalar/array
roots fail. Final snapshots cross-check deltas; they never replace missing streamed evidence.

## Principal findings

| Code | Meaning |
|---|---|
| DUPLICATE_CALL / DUPLICATE_EXECUTION | Reused call identity or repeated execution |
| CALL_MISMATCH / EXECUTION_MISMATCH | Names, arguments or multiplicity differ |
| INVALID_ARGUMENTS / SNAPSHOT_MISMATCH | Invalid JSON or lost/changed delta data |
| MISSING_TOOL_END / MISSING_FINISH / MISSING_TERMINAL | Missing completion evidence |
| PREMATURE_EXECUTION / EXECUTION_ARGUMENTS | Execution before commit or with changed arguments |
| POST_CANCEL / POST_CANCEL_EXECUTION | Work continued after cancellation |
| LATE_EVENT / ATTEMPT_ORDER / ROUTING | Broken attempt isolation or retry/fallback routing |
| RESOURCE_LEAK / MISSING_ATTEMPT_END | Cleanup/outcome evidence absent |
| TRANSPORT_ERROR / PROTOCOL_ERROR | Failed transport or malformed protocol |
| INSUFFICIENT_EVIDENCE | Strict wire gate received weaker SDK evidence |

The oracle returns all findings; one corrupt frame can cause downstream findings. External traces are
trusted instrumentation evidence, not cryptographic proof that remote tools actually executed.

## Protocol references

- [Chat Completions streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
- [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)
- [Anthropic streaming messages](https://platform.claude.com/docs/en/build-with-claude/streaming)

Provider documentation evolves. Add a minimal fixture and regression test when updating a mapping.

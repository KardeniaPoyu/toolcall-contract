# Toolcall Contract

**Catch broken tool-call semantics before a model, SDK, or gateway reaches production.**

One suite. Three streaming protocols. Deterministic faults. CI-readable evidence.

[中文说明](README.zh-CN.md) · [Contract](docs/contract.md) · [Adapters](docs/adapters.md) · [CI](docs/ci.md)

Toolcall Contract checks whether streamed tool calls retain their IDs, arguments, completion events,
and execution multiplicity through cancellation, retry, and fallback. It separates proposed calls
from recorded execution, so replayed output cannot quietly become a second side effect.

This is **0.1 alpha**. Offline and local HTTP/SDK tests are deterministic. Live model results also
reflect the model's ability to follow the exact test prompt.

## Run in one minute

Python 3.11 or newer. The core has **zero runtime dependencies**.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
tcc demo --save-traces
```

The default run executes **27 checks: 9 scenarios × 3 protocols**, without credentials or external
requests. Reports contain JSON, JUnit XML, a Markdown summary and optional replayable traces.

```sh
# A broken stream must fail the gate (exit 1)
tcc demo --mutation argument-loss --scenario basic

# Actual HTTP clients against a loopback SSE endpoint
tcc local

# Also test real Python SDKs (54 checks total)
python -m pip install -e ".[sdk]"
tcc local --sdk --report-dir reports/local

# Audit evidence without calling the model again
tcc replay reports/000-chat-basic.trace.json --report-dir reports/replay
```

Commands also work as `python -m toolcall_contract`. Use `tcc list` to see scenarios.

## Acceptance contract

| Area | Checks |
|---|---|
| Streaming | Arbitrary SSE/UTF-8 boundaries, fragmented JSON, interleaved indexes |
| Identity | Stable nonempty ID/name; no ID reuse within an attempt |
| Arguments | Strict JSON objects, no duplicate keys/NaN, exact typed payload and multiplicity |
| Completion | Tool completion, response finish and stream terminal evidence |
| Cancellation | Close the source; no subsequent delivery, execution, or retry |
| Retry | Fresh decoder/state; discard interrupted calls; execute only the accepted attempt |
| Fallback | Same expectations on a distinct target; no cross-attempt parameter merging |
| Execution | No partial/invalid execution, duplicate execution or modified arguments |
| Resources | Close and outcome on every attempt; event/byte/time limits |

Scenarios: basic, Unicode/escaping, nested arguments, parallel calls, cancel mid-arguments, retry
mid-arguments, fallback mid-arguments, retry after tool completion and cancel after tool completion.
The last two cut at tool completion **before the entire response commits**.

## Protocol coverage

| Protocol | Raw HTTP | Python SDK | Completion evidence |
|---|---|---|---|
| Chat Completions | Yes | `openai` | finish reason + `[DONE]`; SDK substitutes iterator EOF |
| Responses | Yes | `openai` | argument done + output item done + response completed |
| Anthropic Messages | Yes | `anthropic` | content block stop + stop reason + message stop |

Chat SDK results are labeled **`sdk-eof`** because they cannot establish whether `[DONE]` existed on
the wire. Include a companion raw HTTP target, or use `probe --require-wire` for strict wire gates.
SDK Responses/Anthropic use `sdk-events`; their byte framing remains hidden by the SDK.

## Probe a model or gateway

Copy `examples/targets.json` to `targets.local.json`, set your API root/model, and provide the named
environment variable. `base_url` is normally the root ending in `/v1`, without an operation suffix.

```powershell
$env:MODEL_API_KEY = "your-key"
tcc probe --config targets.local.json --report-dir reports/live
```

```sh
export MODEL_API_KEY='your-key'
tcc probe --config targets.local.json --report-dir reports/live
```

Use `examples/matrix.json` for multiple targets and fallback. The runner records synthetic executions
and **never invokes business functions**. Live probes incur provider usage. A full nine-scenario run
makes up to **12 requests per target**. Credentials are never read from other applications.

Other SDKs/languages can export canonical traces and run `tcc replay`. To test consumers against a
broken endpoint, run `tcc serve --mutation missing-terminal`. See [adapters](docs/adapters.md).

## CI

```yaml
- run: python -m pip install .
- run: tcc demo --report-dir reports
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: toolcall-contract
    path: reports/
```

Exit codes: **0** all pass; **1** semantic/transport failure; **2** invalid input/configuration;
**130** user interrupt. Empty selections never pass. Default reports omit arguments and server error
bodies. `--save-traces` explicitly saves arguments for debugging/replay.

Included workflows test Python 3.11–3.13 on Linux/Windows, lint, build distributions and exercise SDKs.
Real endpoint tests use an explicit manual workflow and repository secrets.

## Scope and limits

- Retry/fallback scenarios inject a **client-side disconnect** and test this runner's orchestration.
  They cannot observe a gateway's internal retries or routing. Instrument that layer and replay its
  trace to audit actual execution behavior.
- SDK automatic retries are disabled. Unexpected 429s/timeouts/provider errors fail the gate. This
  version does not implement configurable HTTP backoff or server cancellation acknowledgement.
- Cancellation occurs synchronously at observed event boundaries. It verifies close and withholding
  execution, not provider billing cancellation, asynchronous race safety or hard real-time latency.
- The recorder stages calls until a valid, closed stream. Production side effects still need durable
  idempotency and crash recovery; this suite cannot guarantee distributed exactly-once execution.
- Exact expected arguments catch loss but also expose model nondeterminism/refusals/unsupported
  parallel calling. Compare direct provider, gateway and SDK results to locate the responsible layer.
- Supported: single response choice, client function tools, one assistant turn. Legacy `function_call`,
  server tools, Realtime/WebSockets and multi-turn tool-result loops are outside 0.1.

## Development

```sh
python -m pip install -e ".[test,sdk]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

Tests include corrupted streams, randomized byte partitions, lifecycle trace attacks, real HTTP faults,
SDK integration, and known SDK/wire evidence differences. [Contributing](CONTRIBUTING.md). MIT licensed.

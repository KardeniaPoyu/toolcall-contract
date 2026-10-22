# Adapter integration

## Python sources

`run(scenario, target, fallback=None, factory=source_for)` accepts `(Target, Scenario) -> source`.
The source iterates `Event` objects and supplies `close()`, `evidence` and a `metadata` dictionary.
Each factory invocation must construct fresh state; never reuse stream buffers/normalizers on retry.

```python
from toolcall_contract.runner import run
from toolcall_contract.scenarios import suite
from toolcall_contract.transport import Target


class MySource:
    evidence = "external-sdk"
    metadata = {"sdk": "example", "sdk_version": "1.0"}

    def __init__(self, target, scenario):
        self.stream = open_your_sdk_stream(target, scenario)

    def __iter__(self):
        for item in self.stream:
            yield normalize_your_sdk_event(item)

    def close(self):
        self.stream.close()


result = run(suite()[0], Target("my-sdk"), factory=MySource)
assert result.passed, result.findings
```

This illustrates the interface: the two SDK functions are supplied by the integration. Let errors
propagate. Close the underlying resource, not just its generator. Include actual SDK versions. Never
synthesize provider finish on EOF; explicitly declare weaker evidence if the SDK hides a terminal.

## Existing agents, gateways and other languages

`run()` tests the reference runner's policy. To test an application's own retry/execution policy,
instrument its actual events, close calls, attempts and executions. Export the [v1 trace](contract.md)
and run `tcc replay trace.json`. This works for TypeScript, Go, Java and gateway internals.

Do not deduplicate/repair the evidence before auditing. Generated tool calls are not proof of actual
execution. Synthetic executions from the reference runner must not be mislabeled as business effects.

## Local server

```sh
tcc serve --port 8765 --fragment-bytes 1
tcc serve --port 8765 --mutation duplicate-call
tcc serve --port 8765 --mutation disconnect
tcc serve --port 8765 --mutation http-429
```

The loopback-only server implements `/v1/chat/completions`, `/v1/responses`, `/v1/messages`. It accepts
the synthetic request shape from `request_body()`, not general model prompts. `--delay` inserts time
between writes. TCP may coalesce writes; parser tests independently enforce arbitrary byte splits.

`tcc local --sdk` launches an ephemeral server and uses actual SDKs with `max_retries=0`. Missing SDKs
fail this command. Pytest skips optional SDK tests explicitly; the included CI installs SDK extras.

## Transport boundaries

- Require HTTPS except on loopback. Credentials come only from a named environment variable.
- Redirects are rejected. Proxy environment variables retain underlying library behavior.
- `timeout` bounds a blocking network operation. `deadline` is checked between reads/events, so a
  blocked read can exceed it by the network timeout. OS DNS behavior is outside that bound.
- `max_bytes` bounds wire bytes for HTTP and serialized events for SDK mode. SSE events have a 1 MiB
  limit; canonical traces stop at 100,000 runner events. SDK internal buffering is SDK-controlled.
- Errors omit server response bodies. Do not upload production payloads or credentials as CI artifacts.

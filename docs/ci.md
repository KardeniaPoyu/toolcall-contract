# CI integration

Keep three questions separate:

1. Suite correctness: `pytest` and `tcc demo` without credentials.
2. SDK consumption: `tcc local --sdk` against controlled streams and mutation endpoints.
3. Endpoint conformance: `tcc probe` against explicit model/gateway targets.

Use distinct targets for direct provider, gateway and SDK comparisons. Keep scenarios identical.
Include raw HTTP targets for wire termination evidence. `--require-wire` rejects SDK-only evidence.

## Configuration

`schema_version: 1` is required. Unknown top-level/target keys fail. Target fields: `name`, `protocol`
(`chat|responses|anthropic`), `transport` (`http|sdk|fixture`), `base_url`, `model`, `api_key_env`,
`timeout` (15 seconds), `deadline` (60 seconds), `max_bytes` (8 MiB). Names must be unique.

`scenarios` selects a nonempty list from `tcc list`; omitting it selects the full suite. Fallback
scenarios require every target to have a distinct destination in `fallbacks`. The destination is also
tested as a primary; use both directions in a two-target matrix.

Unexpected 429/network/protocol errors fail. Planned retry/fallback scenarios inject their own
interruption for deterministic lifecycle expectations; the suite does not silently retry failures.

## GitHub Actions

`ci.yml` is credential-free, runs on pull requests, installs SDKs and builds wheel/sdist. Reports are
uploaded even on failures. `live.yml` is manual (`workflow_dispatch`). Configure variables
`MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_PROTOCOL` and secret `MODEL_API_KEY`. Its Python step reads
environment values without interpolating them into shell code.

The live workflow tests normal/cancel/retry with raw HTTP. Fallback needs a second configured target;
start from `examples/matrix.json`. Fork pull requests cannot access these secrets or spend API credits.
No `pull_request_target` is used. Raw argument traces are opt-in.

## Reproducibility

The core is standard-library-only. SDK dependency ranges are bounded; reports record installed SDK
versions. `constraints/sdk-tested.txt` pins the direct SDK/client versions initially tested:

```sh
python -m pip install -c constraints/sdk-tested.txt ".[test,sdk]"
```

These constraints are not a transitive lockfile. Maintain your own full lockfile for repeatable CI on
your platform/package index; change SDKs independently of gateways when diagnosing regressions.

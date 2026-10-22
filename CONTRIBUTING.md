# Contributing

Install `.[test,sdk]`, run `pytest`, `ruff check .` and `ruff format --check .` before submitting.
Use Python 3.11-compatible syntax; keep the core dependency-free and SDK imports optional/lazy.

For bugs, add a sanitized minimal trace/fixture, an assertion naming the expected finding and a
neighboring positive case. Keep protocol normalization separate from invariants. Never insert missing
finish events, deduplicate calls, or replace lost deltas with final snapshots to make tests pass.

Document evidence limits and new mappings. Version incompatible trace/report schema changes. Do not
commit credentials, production arguments or local reports. SDK upgrades need integration and mutation
tests. PRs should explain the observable bug, corrected behavior and relevant validation.

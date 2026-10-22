from dataclasses import replace

import pytest

from toolcall_contract.fixtures import MUTATIONS
from toolcall_contract.model import Call, Event, Scenario
from toolcall_contract.oracle import audit
from toolcall_contract.protocols import PROTOCOLS
from toolcall_contract.runner import run
from toolcall_contract.scenarios import suite
from toolcall_contract.transport import FixtureSource, Target


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("scenario", suite(), ids=lambda s: s.name)
def test_reference_contract(protocol, scenario):
    result = run(
        scenario, Target("primary", protocol, "fixture"), Target("backup", protocol, "fixture")
    )
    assert result.passed, result.findings
    assert sum(e.kind == "execute" for e in result.events) == (
        0 if scenario.mode == "cancel" else len(scenario.calls)
    )


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_wire_mutations_are_rejected(protocol, mutation):
    if mutation == "snapshot-mismatch" and protocol != "responses":
        pytest.skip("only Responses carries a final argument snapshot")
    result = run(
        suite()[0],
        Target("bad", protocol, "fixture"),
        factory=lambda t, s: FixtureSource(t, s, mutation),
    )
    assert not result.passed
    assert not any(e.kind == "execute" for e in result.events)
    expected = {
        "duplicate-call": "DUPLICATE_CALL",
        "argument-loss": "CALL_MISMATCH",
        "missing-terminal": "MISSING_TERMINAL",
        "missing-finish": "MISSING_FINISH",
        "identity-swap": "PROTOCOL_ERROR" if protocol != "anthropic" else "ORPHAN_ARGUMENTS",
        "truncated-json": "INVALID_ARGUMENTS",
        "duplicate-finish": "DUPLICATE_FINISH",
        "snapshot-mismatch": "SNAPSHOT_MISMATCH",
    }
    assert expected[mutation] in {f.code for f in result.findings}


def good_trace(scenario=None):
    scenario = scenario or suite()[0]
    return run(scenario, Target("a", "chat", "fixture"), Target("b", "responses", "fixture"))


def test_duplicate_execution_across_retry_attempts():
    result = good_trace(suite()[5])
    execution = next(e for e in result.events if e.kind == "execute")
    result.events.append(execution)
    assert "DUPLICATE_EXECUTION" in {f.code for f in audit(result.scenario, result.events)}


def test_cancel_forbids_execution_and_retry():
    result = good_trace(suite()[4])
    result.events += [
        Event("execute", 0, "0", "call_0", "record", {}),
        Event("attempt_start", 1, data="a"),
    ]
    codes = {f.code for f in audit(result.scenario, result.events)}
    assert {"POST_CANCEL_EXECUTION", "POST_CANCEL"} <= codes


@pytest.mark.parametrize(
    "kind,code",
    [
        ("close", "RESOURCE_LEAK"),
        ("attempt_end", "MISSING_ATTEMPT_END"),
        ("stream_end", "MISSING_TERMINAL"),
        ("tool_end", "MISSING_TOOL_END"),
    ],
)
def test_missing_lifecycle_evidence(kind, code):
    result = good_trace()
    events = [e for e in result.events if e.kind != kind]
    assert code in {f.code for f in audit(result.scenario, events)}


def test_execution_before_commit_rejected():
    result = good_trace()
    execution = result.events.pop()
    result.events.insert(2, execution)
    assert "PREMATURE_EXECUTION" in {f.code for f in audit(result.scenario, result.events)}


@pytest.mark.parametrize(
    "raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', "[1,2]", "null", '{"x":']
)
def test_strict_arguments(raw):
    result = good_trace()
    events = [e for e in result.events if e.kind != "args"]
    events.insert(2, Event("args", slot="0", data=raw))
    assert "INVALID_ARGUMENTS" in {f.code for f in audit(result.scenario, events)}


def test_identical_parallel_calls_are_legitimate_if_contract_expects_two():
    call = Call("record", {"x": 1})
    result = good_trace(Scenario("intentional-duplicates", (call, call)))
    assert result.passed


def test_reused_id_is_isolated_across_attempts_and_protocols():
    result = good_trace(suite()[6])
    assert result.passed, result.findings
    assert len({e.call_id for e in result.events if e.kind == "tool_start"}) == 1


def test_late_old_attempt_data_is_not_merged():
    result = good_trace(suite()[5])
    index = next(
        i for i, e in enumerate(result.events) if e.kind == "attempt_start" and e.attempt == 1
    )
    result.events.insert(index + 1, Event("args", 0, "0", data="late"))
    assert "LATE_EVENT" in {f.code for f in audit(result.scenario, result.events)}


def test_fallback_must_change_target():
    result = good_trace(suite()[6])
    events = [replace(e, data="a") if e.kind == "attempt_start" else e for e in result.events]
    assert "ROUTING" in {f.code for f in audit(result.scenario, events)}


def test_execution_argument_mutation():
    result = good_trace()
    events = [replace(e, data={"lost": True}) if e.kind == "execute" else e for e in result.events]
    assert "EXECUTION_ARGUMENTS" in {f.code for f in audit(result.scenario, events)}


def test_close_error_cannot_pass():
    class Leaky(FixtureSource):
        def close(self):
            raise OSError("secret must never appear in report")

    result = run(suite()[0], Target("bad", "chat", "fixture"), factory=Leaky)
    assert not result.passed
    assert "RESOURCE_LEAK" in {f.code for f in result.findings}
    assert "secret" not in repr(result.summary())


def test_handwritten_contract_not_derived_from_fixture_encoder():
    scenario = Scenario("independent", (Call("add", {"x": 1}),))
    events = [
        Event("attempt_start", data="a"),
        Event("tool_start", slot="7", call_id="id", name="add"),
        Event("args", slot="7", data='{"x":'),
        Event("args", slot="7", data="1}"),
        Event("tool_end", slot="7"),
        Event("finish", data="tool_calls"),
        Event("stream_end"),
        Event("close"),
        Event("attempt_end", data="success"),
        Event("execute", slot="7", call_id="id", name="add", data={"x": 1}),
    ]
    assert audit(scenario, events) == []
    events.insert(-1, Event("args", slot="7", data="late"))
    assert "LATE_EVENT" in {f.code for f in audit(scenario, events)}


def test_cancel_closes_actual_source_without_draining_it():
    sources = []

    class Observed(FixtureSource):
        def __init__(self, target, scenario):
            super().__init__(target, scenario)
            self.drained = False
            self.close_count = 0
            sources.append(self)

        def __iter__(self):
            yield from super().__iter__()
            self.drained = True

        def close(self):
            self.close_count += 1
            super().close()

    result = run(suite()[4], Target("cancel", "chat", "fixture"), factory=Observed)
    assert result.passed
    assert sources[0].closed and sources[0].close_count == 1 and not sources[0].drained

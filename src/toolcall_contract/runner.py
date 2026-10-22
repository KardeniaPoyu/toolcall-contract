"""Reference orchestration: stage arguments, close transport, validate, then record execution."""

import time
from dataclasses import replace

from .model import Event, Finding, Result, Scenario, strict_json
from .oracle import audit
from .transport import Target, source_for


def run(
    scenario: Scenario, target: Target, fallback: Target | None = None, factory=source_for
) -> Result:
    if scenario.mode == "fallback" and (fallback is None or fallback.name == target.name):
        raise ValueError("fallback scenario requires a distinct named fallback target")
    events = []
    started = time.monotonic()
    evidence, metadata = "unavailable", {}
    count = 2 if scenario.mode in {"retry", "fallback"} else 1
    for attempt in range(count):
        current = fallback if attempt and scenario.mode == "fallback" else target
        events.append(Event("attempt_start", attempt, data=current.name))
        source, status, deltas = None, "success", 0
        try:
            source = factory(current, scenario)
            evidence = source.evidence
            metadata[str(attempt)] = {
                **source.metadata,
                "target": current.name,
                "protocol": current.protocol,
                "model": current.model,
            }
            for event in source:
                if len(events) > 100_000:
                    raise ValueError("canonical event limit exceeded")
                event = replace(event, attempt=attempt)
                events.append(event)
                if event.kind == "args" and event.data:
                    deltas += 1
                cut = event.kind == "args" and event.data and deltas >= scenario.cut_after
                cut = cut or (scenario.cut_after == 10_000 and event.kind == "tool_end")
                if attempt == 0 and scenario.mode != "normal" and cut:
                    status = "cancelled" if scenario.mode == "cancel" else "interrupted"
                    events.append(
                        Event(
                            "cancel" if status == "cancelled" else "fault",
                            attempt,
                            data=None if status == "cancelled" else "injected_disconnect",
                        )
                    )
                    break
        except Exception as exc:
            # Never persist provider exception bodies, which may contain credentials or user data.
            events.append(Event("fault", attempt, data=type(exc).__name__))
            status = "failed"
        finally:
            try:
                if source is not None:
                    source.close()
                events.append(Event("close", attempt))
            except Exception:
                events.append(Event("violation", attempt, data="Transport close failed"))
                status = "failed"
        events.append(Event("attempt_end", attempt, data=status))
        if status == "success":
            # Only missing execution evidence is expected before commit. Any other finding blocks it.
            errors = audit(scenario, events)
            if not any(f.code not in {"EXECUTION_MISMATCH", "SCENARIO_LIFECYCLE"} for f in errors):
                calls = {}
                for e in events:
                    if e.attempt != attempt:
                        continue
                    if e.kind == "tool_start":
                        calls[e.slot] = [e, ""]
                    elif e.kind == "args" and e.slot in calls:
                        calls[e.slot][1] += e.data
                for slot, (begin, raw) in calls.items():
                    events.append(
                        Event("execute", attempt, slot, begin.call_id, begin.name, strict_json(raw))
                    )
            break
        if status != "interrupted":
            break
    findings = audit(scenario, events)
    if evidence == "unavailable" and not findings:
        findings.append(Finding("NO_EVIDENCE", "No stream evidence was collected"))
    return Result(
        scenario, target.name, events, findings, time.monotonic() - started, evidence, metadata
    )

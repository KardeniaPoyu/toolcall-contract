"""Audit observed evidence. Never repair, merge, or deduplicate a broken trace."""

from collections import Counter

from .model import Call, Event, Finding, Scenario, canonical, strict_json


def audit(scenario: Scenario, events: list[Event]) -> list[Finding]:
    findings: list[Finding] = []
    attempts: dict[int, dict] = {}
    active = None
    cancelled = False
    executions = Counter()
    expected = Counter(c.signature() for c in scenario.calls)

    def fail(code, message, event, index):
        findings.append(Finding(code, message, event.attempt, index))

    for i, e in enumerate(events):
        if e.kind == "attempt_start":
            if active is not None or e.attempt in attempts or e.attempt != len(attempts):
                fail("ATTEMPT_ORDER", "Attempts must be sequential and non-overlapping", e, i)
            if cancelled:
                fail("POST_CANCEL", "A new attempt started after cancellation", e, i)
            if attempts and any(a["status"] == "success" for a in attempts.values()):
                fail("RETRY_AFTER_COMMIT", "A successful attempt was replayed", e, i)
            attempts[e.attempt] = {
                "calls": {},
                "ids": set(),
                "finish": False,
                "terminal": False,
                "status": None,
                "closed": False,
                "target": e.data,
                "executed": set(),
            }
            active = e.attempt
            continue
        if e.attempt not in attempts:
            fail("ORPHAN_EVENT", "Event has no attempt_start", e, i)
            continue
        a = attempts[e.attempt]
        calls = a["calls"]
        if e.kind == "execute":
            c = calls.get(e.slot)
            if cancelled:
                fail("POST_CANCEL_EXECUTION", "Tool executed after cancellation", e, i)
            if a["status"] != "success" or not a["closed"] or not a["terminal"]:
                fail("PREMATURE_EXECUTION", "Execution requires a closed, successful stream", e, i)
            if e.slot in a["executed"]:
                fail("DUPLICATE_EXECUTION", "The same call was executed more than once", e, i)
            a["executed"].add(e.slot)
            if not c or not c.get("done") or e.call_id != c["id"] or e.name != c["name"]:
                fail("EXECUTION_IDENTITY", "Execution does not match a completed call", e, i)
            if not isinstance(e.data, dict):
                fail("EXECUTION_ARGUMENTS", "Execution arguments must be an object", e, i)
            else:
                executions[Call(e.name, e.data).signature()] += 1
                if (
                    c
                    and c.get("parsed") is not None
                    and canonical(e.data) != canonical(c["parsed"])
                ):
                    fail(
                        "EXECUTION_ARGUMENTS",
                        "Arguments changed between generation and execution",
                        e,
                        i,
                    )
            continue
        if e.kind == "close":
            if a["closed"]:
                fail("DUPLICATE_CLOSE", "Transport closed more than once", e, i)
            a["closed"] = True
            continue
        if e.kind == "attempt_end":
            if a["status"] is not None or active != e.attempt:
                fail("ATTEMPT_ORDER", "Unexpected attempt_end", e, i)
            a["status"] = e.data
            active = None
            if e.data not in {"success", "cancelled", "interrupted", "failed"}:
                fail("ATTEMPT_STATUS", "Unknown attempt outcome", e, i)
            if e.data == "success":
                if not a["finish"]:
                    fail("MISSING_FINISH", "Successful attempt has no finish event", e, i)
                if not a["terminal"]:
                    fail("MISSING_TERMINAL", "Successful attempt has no stream end", e, i)
                observed = Counter()
                for c in calls.values():
                    if not c["done"]:
                        fail("MISSING_TOOL_END", "Tool arguments have no completion event", e, i)
                    if c.get("parsed") is not None:
                        observed[Call(c["name"], c["parsed"]).signature()] += 1
                if observed != expected:
                    fail(
                        "CALL_MISMATCH",
                        "Tool names, arguments, or multiplicities differ from the contract",
                        e,
                        i,
                    )
            continue
        if a["status"] is not None or a["closed"] or active != e.attempt:
            fail("LATE_EVENT", "Stream event arrived outside its active attempt", e, i)
        if e.kind == "cancel":
            cancelled = True
            continue
        if e.kind == "fault":
            if e.data != "injected_disconnect":
                fail(
                    "TRANSPORT_ERROR",
                    "Transport failed; inspect endpoint locally for details",
                    e,
                    i,
                )
            continue
        if cancelled:
            fail("POST_CANCEL", "Stream continued after cancellation", e, i)
        if a["terminal"]:
            fail("AFTER_TERMINAL", "Event arrived after stream end", e, i)
        if e.kind == "tool_start":
            if a["finish"]:
                fail("AFTER_FINISH", "Tool started after finish", e, i)
            if e.slot in calls or (e.call_id and e.call_id in a["ids"]):
                fail("DUPLICATE_CALL", "Tool slot or call ID was reused within an attempt", e, i)
            if not e.call_id or not e.name:
                fail("MISSING_IDENTITY", "Tool start requires both ID and function name", e, i)
            a["ids"].add(e.call_id)
            calls[e.slot] = {"id": e.call_id, "name": e.name, "raw": "", "done": False}
        elif e.kind in {"args", "tool_end"}:
            c = calls.get(e.slot)
            if c is None:
                fail("ORPHAN_ARGUMENTS", "Arguments refer to an unknown tool slot", e, i)
                continue
            if c["done"]:
                fail("AFTER_TOOL_END", "Tool received data or a second end after completion", e, i)
            if a["finish"]:
                fail("AFTER_FINISH", "Arguments arrived after finish", e, i)
            if e.kind == "args":
                if not isinstance(e.data, str):
                    fail("ARGUMENT_TYPE", "Argument delta must be text", e, i)
                else:
                    c["raw"] += e.data
            else:
                c["done"] = True
                try:
                    c["parsed"] = strict_json(c["raw"])
                    if not isinstance(c["parsed"], dict):
                        raise ValueError("not an object")
                    if e.data is not None and canonical(strict_json(e.data)) != canonical(
                        c["parsed"]
                    ):
                        fail(
                            "SNAPSHOT_MISMATCH",
                            "Final arguments do not match accumulated deltas",
                            e,
                            i,
                        )
                except (ValueError, TypeError):
                    c["parsed"] = None
                    fail("INVALID_ARGUMENTS", "Arguments are not a strict JSON object", e, i)
        elif e.kind == "finish":
            if a["finish"]:
                fail("DUPLICATE_FINISH", "More than one finish event", e, i)
            a["finish"] = True
            if e.data not in {"tool_calls", "tool_use", "completed"}:
                fail("FINISH_REASON", "Response did not finish with complete tool calls", e, i)
        elif e.kind == "stream_end":
            a["terminal"] = True
        elif e.kind == "violation":
            fail("PROTOCOL_ERROR", str(e.data), e, i)
        else:
            fail("UNKNOWN_EVENT", "Unrecognized canonical event kind", e, i)

    sentinel = Event("eof")
    if not attempts:
        fail("EMPTY_TRACE", "No attempts were observed", sentinel, -1)
    for number, a in attempts.items():
        e = Event("eof", attempt=number)
        if not a["closed"]:
            fail("RESOURCE_LEAK", "Transport close was not observed", e, -1)
        if a["status"] is None:
            fail("MISSING_ATTEMPT_END", "Attempt has no outcome", e, -1)
    statuses = [a["status"] for a in attempts.values()]
    wanted = {
        "normal": ["success"],
        "cancel": ["cancelled"],
        "retry": ["interrupted", "success"],
        "fallback": ["interrupted", "success"],
    }
    if statuses != wanted[scenario.mode] or cancelled != (scenario.mode == "cancel"):
        fail("SCENARIO_LIFECYCLE", "Attempt outcomes do not satisfy this scenario", sentinel, -1)
    if scenario.mode in {"retry", "fallback"} and len(attempts) == 2:
        targets = [a["target"] for a in attempts.values()]
        if (targets[0] == targets[1]) != (scenario.mode == "retry"):
            fail("ROUTING", "Retry must retain target; fallback must change target", sentinel, -1)
    if executions != (Counter() if scenario.mode == "cancel" else expected):
        fail(
            "EXECUTION_MISMATCH",
            "Execution count or arguments differ from the contract",
            sentinel,
            -1,
        )
    return findings

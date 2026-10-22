"""Deterministic wire fixtures and mutations. No credentials or network required."""

from itertools import zip_longest

from .model import Scenario, canonical

MUTATIONS = (
    "duplicate-call",
    "argument-loss",
    "missing-terminal",
    "missing-finish",
    "identity-swap",
    "truncated-json",
    "duplicate-finish",
    "snapshot-mismatch",
)


def wire_events(protocol: str, scenario: Scenario, mutation: str | None = None) -> list[dict | str]:
    calls = list(scenario.calls)
    if mutation == "duplicate-call":
        calls.append(calls[0])
    raw = [canonical(c.arguments) for c in calls]
    if mutation == "argument-loss":
        raw[0] = canonical({k: v for k, v in calls[0].arguments.items() if k != "payload"})
    if mutation == "truncated-json":
        raw[0] = raw[0][:-1]
    chunks = [[text[i : i + 7] for i in range(0, len(text), 7)] for text in raw]
    out: list[dict | str] = []
    items = []
    if protocol == "anthropic":
        out.append(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            }
        )
    if protocol == "responses":
        out.append(
            {
                "type": "response.created",
                "response": {"id": "resp_fixture", "status": "in_progress"},
            }
        )
    for i, call in enumerate(calls):
        call_id = f"call_{i}"
        if mutation == "duplicate-call" and i == len(calls) - 1:
            call_id = "call_0"
        if protocol == "chat":
            out.append(
                chat(
                    {
                        "tool_calls": [
                            {
                                "index": i,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": ""},
                            }
                        ]
                    }
                )
            )
        elif protocol == "responses":
            item = {
                "id": f"fc_{i}",
                "type": "function_call",
                "call_id": call_id,
                "name": call.name,
                "arguments": "",
                "status": "in_progress",
            }
            items.append(item)
            out.append(
                {"type": "response.output_item.added", "output_index": i, "item": item.copy()}
            )
        else:
            out.append(
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": {
                        "type": "tool_use",
                        "id": call_id,
                        "name": call.name,
                        "input": {},
                    },
                }
            )
    for row in zip_longest(*chunks):
        for i, fragment in enumerate(row):
            if fragment is None:
                continue
            if protocol == "chat":
                tc = {"index": i, "function": {"arguments": fragment}}
                if mutation == "identity-swap":
                    tc["id"] = "changed"
                out.append(chat({"tool_calls": [tc]}))
            elif protocol == "responses":
                out.append(
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": i,
                        "item_id": "changed" if mutation == "identity-swap" else f"fc_{i}",
                        "delta": fragment,
                    }
                )
            else:
                out.append(
                    {
                        "type": "content_block_delta",
                        "index": 999 if mutation == "identity-swap" else i,
                        "delta": {"type": "input_json_delta", "partial_json": fragment},
                    }
                )
    for i, text in enumerate(raw):
        if protocol == "responses":
            out.append(
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": i,
                    "item_id": f"fc_{i}",
                    "arguments": "{}" if mutation == "snapshot-mismatch" else text,
                }
            )
            items[i].update(arguments=text, status="completed")
            out.append(
                {"type": "response.output_item.done", "output_index": i, "item": items[i].copy()}
            )
        elif protocol == "anthropic":
            out.append({"type": "content_block_stop", "index": i})
    if protocol == "chat":
        finish = chat({}, "tool_calls")
        terminal = "[DONE]"
    elif protocol == "responses":
        finish = {
            "type": "response.completed",
            "response": {"id": "resp_fixture", "status": "completed", "output": items},
        }
        terminal = None  # completion is both semantic finish and stream terminal
    else:
        finish = {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 10},
        }
        terminal = {"type": "message_stop"}
    if mutation != "missing-finish" and not (
        protocol == "responses" and mutation == "missing-terminal"
    ):
        out.append(finish)
        if mutation == "duplicate-finish":
            out.append(finish)
    if terminal and mutation != "missing-terminal":
        out.append(terminal)
    return out


def chat(delta, reason=None):
    return {
        "id": "chatcmpl_fixture",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "fixture",
        "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
    }


def encode_sse(events: list, protocol: str) -> bytes:
    parts = [": heartbeat\r\n\r\n"]
    for number, event in enumerate(events):
        if isinstance(event, dict) and protocol == "responses":
            event = {**event, "sequence_number": number}
        prefix = (
            f"event: {event['type']}\r\n" if isinstance(event, dict) and "type" in event else ""
        )
        parts.append(
            prefix + "data: " + (event if isinstance(event, str) else canonical(event)) + "\r\n\r\n"
        )
    return "".join(parts).encode("utf-8")

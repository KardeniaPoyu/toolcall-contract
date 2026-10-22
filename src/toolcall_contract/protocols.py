"""Wire/SDK event normalization without silently synthesizing missing terminal events."""

from .model import Event, canonical, strict_json
from .sse import Frame

PROTOCOLS = ("chat", "responses", "anthropic")


class Normalizer:
    def __init__(self, protocol: str):
        if protocol not in PROTOCOLS:
            raise ValueError("unsupported protocol")
        self.protocol = protocol
        self.calls: dict[str, dict] = {}
        self.started = False

    def feed(self, frame: Frame) -> list[Event]:
        if frame.data == "[DONE]":
            return (
                [Event("stream_end")]
                if self.protocol == "chat"
                else [self.bad("Unexpected DONE sentinel")]
            )
        data = strict_json(frame.data)
        if not isinstance(data, dict):
            raise ValueError("event must be a JSON object")
        if "error" in data or data.get("type") == "error":
            return [self.bad("Provider emitted an error event")]
        return getattr(self, "_" + self.protocol)(data)

    @staticmethod
    def bad(message):
        return Event("violation", data=message)

    def _chat(self, data):
        out = []
        for choice in data.get("choices", []):
            if choice.get("index", 0) != 0:
                out.append(self.bad("Only a single completion choice is supported"))
                continue
            delta = choice.get("delta", {})
            if "function_call" in delta:
                out.append(self.bad("Legacy function_call is not supported"))
            for tc in delta.get("tool_calls", []):
                if "index" not in tc:
                    out.append(self.bad("Tool delta is missing index"))
                    continue
                slot = str(tc["index"])
                fn = tc.get("function", {})
                if slot not in self.calls:
                    self.calls[slot] = {"id": tc.get("id", ""), "name": fn.get("name", "")}
                    out.append(
                        Event(
                            "tool_start",
                            slot=slot,
                            call_id=tc.get("id", ""),
                            name=fn.get("name", ""),
                        )
                    )
                else:
                    c = self.calls[slot]
                    if (tc.get("id") and tc["id"] != c["id"]) or (
                        fn.get("name") and fn["name"] != c["name"]
                    ):
                        out.append(self.bad("Tool identity changed within an index"))
                if fn.get("arguments") is not None:
                    out.append(Event("args", slot=slot, data=fn["arguments"]))
            if choice.get("finish_reason") is not None:
                # Chat has no per-tool done event: finish_reason is its completion evidence.
                out.extend(Event("tool_end", slot=s) for s in self.calls)
                out.append(Event("finish", data=choice["finish_reason"]))
        return out

    def _responses(self, data):
        kind = data.get("type", "")
        slot = str(data.get("output_index", ""))
        if kind == "response.output_item.added":
            item = data["item"]
            if item.get("type") != "function_call":
                return []
            self.calls[slot] = {
                "id": item["call_id"],
                "name": item["name"],
                "item_id": item["id"],
                "done": False,
                "args": None,
            }
            out = [Event("tool_start", slot=slot, call_id=item["call_id"], name=item["name"])]
            if item.get("arguments"):
                out.append(Event("args", slot=slot, data=item["arguments"]))
            return out
        if kind in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            c = self.calls.get(slot)
            if not c or c["item_id"] != data.get("item_id"):
                return [self.bad("Argument event has unknown item ID/index")]
            if kind.endswith(".delta"):
                return [Event("args", slot=slot, data=data["delta"])]
            c["args"] = data["arguments"]
            return [Event("tool_end", slot=slot, data=data["arguments"])]
        if kind == "response.output_item.done":
            item = data["item"]
            if item.get("type") != "function_call":
                return []
            c = self.calls.get(slot)
            if not c or c["done"]:
                return [self.bad("Missing or duplicate function output item")]
            c["done"] = True
            if (item.get("id"), item.get("call_id"), item.get("name"), item.get("arguments")) != (
                c["item_id"],
                c["id"],
                c["name"],
                c["args"],
            ):
                return [self.bad("Function output snapshot changed or completion is missing")]
            return []
        if kind == "response.completed":
            response = data["response"]
            out = []
            if response.get("status") != "completed" or any(
                not c["done"] for c in self.calls.values()
            ):
                out.append(self.bad("Response completed without completed output items"))
            snapshots = [
                (x.get("call_id"), x.get("name"), x.get("arguments"))
                for x in response.get("output", [])
                if x.get("type") == "function_call"
            ]
            wanted = [(c["id"], c["name"], c["args"]) for c in self.calls.values()]
            if sorted(snapshots, key=repr) != sorted(wanted, key=repr):
                out.append(self.bad("Final response snapshot does not match streamed calls"))
            return out + [Event("finish", data="completed"), Event("stream_end")]
        if kind in {"response.failed", "response.incomplete"}:
            return [Event("finish", data=kind), Event("stream_end")]
        if kind in {"response.created", "response.in_progress"} or kind.startswith(
            (
                "response.output_text.",
                "response.content_part.",
                "response.reasoning",
                "response.refusal.",
            )
        ):
            return []
        return [self.bad("Unsupported Responses event")]

    def _anthropic(self, data):
        kind = data.get("type", "")
        slot = str(data.get("index", ""))
        if kind == "message_start":
            if self.started:
                return [self.bad("Duplicate message_start")]
            self.started = True
            return []
        if kind == "content_block_start":
            block = data["content_block"]
            if block["type"] != "tool_use":
                return []
            self.calls[slot] = {"deltas": False, "initial": block.get("input", {})}
            return [Event("tool_start", slot=slot, call_id=block["id"], name=block["name"])]
        if kind == "content_block_delta" and data["delta"]["type"] == "input_json_delta":
            c = self.calls.get(slot)
            if c:
                c["deltas"] = True
                if c["initial"]:
                    return [self.bad("Nonempty initial tool input mixed with deltas")]
            return [Event("args", slot=slot, data=data["delta"]["partial_json"])]
        if kind == "content_block_stop" and slot in self.calls:
            c = self.calls[slot]
            out = [] if c["deltas"] else [Event("args", slot=slot, data=canonical(c["initial"]))]
            return out + [Event("tool_end", slot=slot)]
        if kind == "message_delta" and data.get("delta", {}).get("stop_reason") is not None:
            return [Event("finish", data=data["delta"]["stop_reason"])]
        if kind == "message_stop":
            out = [] if self.started else [self.bad("Missing message_start")]
            return out + [Event("stream_end")]
        if kind in {"ping", "content_block_delta", "content_block_stop", "message_delta"}:
            return []
        return [self.bad("Unsupported Anthropic event")]

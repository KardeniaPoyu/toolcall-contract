"""One semantic contract used by every protocol and transport."""

from .model import Call, Scenario


def suite() -> list[Scenario]:
    simple = Call("record", {"case_id": "basic", "payload": "hello", "sequence": 1})
    unicode = Call(
        "record", {"case_id": "unicode", "payload": '北京 / café / 🧪 / \\"\n', "sequence": 2}
    )
    nested = Call(
        "record",
        {
            "case_id": "nested",
            "payload": {"empty": {}, "items": [0, False, None, ""], "nested": {"value": "a" * 257}},
            "sequence": 3,
        },
    )
    parallel = tuple(
        Call("record", {"case_id": "parallel", "payload": city, "sequence": n})
        for n, city in enumerate(["Tokyo", "Paris", "北京"])
    )
    return [
        Scenario("basic", (simple,)),
        Scenario("unicode-escaping", (unicode,)),
        Scenario("nested-arguments", (nested,)),
        Scenario("parallel-interleaved", parallel),
        Scenario("cancel-mid-arguments", (unicode,), "cancel"),
        Scenario("retry-mid-arguments", (unicode,), "retry"),
        Scenario("fallback-mid-arguments", (nested,), "fallback"),
        Scenario("retry-after-tool-ready", (simple,), "retry", 10_000),
        Scenario("cancel-after-tool-ready", (simple,), "cancel", 10_000),
    ]

"""Versioned, vendor-neutral evidence format. IDs are scoped to an attempt."""

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


def strict_json(raw: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite JSON number")

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict

    def signature(self) -> str:
        return canonical([self.name, self.arguments])


@dataclass(frozen=True)
class Scenario:
    name: str
    calls: tuple[Call, ...]
    mode: str = "normal"
    cut_after: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        if not isinstance(data, dict) or set(data) - {"name", "calls", "mode", "cut_after"}:
            raise ValueError("invalid scenario object")
        if not isinstance(data.get("name"), str) or not data["name"]:
            raise ValueError("scenario needs a name")
        if not isinstance(data.get("calls"), list):
            raise ValueError("scenario calls must be an array")
        if type(data.get("cut_after", 1)) is not int:
            raise ValueError("cut_after must be an integer")
        scenario = cls(
            data["name"],
            tuple(Call(**c) for c in data["calls"]),
            data.get("mode", "normal"),
            data.get("cut_after", 1),
        )
        if scenario.mode not in {"normal", "cancel", "retry", "fallback"}:
            raise ValueError("unknown scenario mode")
        if not scenario.calls or scenario.cut_after < 1:
            raise ValueError("scenario needs calls and a positive cut_after")
        if any(
            not isinstance(c.name, str) or not c.name or not isinstance(c.arguments, dict)
            for c in scenario.calls
        ):
            raise ValueError("calls require a name and object arguments")
        canonical([c.arguments for c in scenario.calls])
        return scenario


@dataclass(frozen=True)
class Event:
    kind: str
    attempt: int = 0
    slot: str = ""
    call_id: str = ""
    name: str = ""
    data: Any = None

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("event must be an object")
        event = cls(**data)
        if type(event.attempt) is not int or event.attempt < 0:
            raise ValueError("attempt must be a nonnegative integer")
        if any(not isinstance(v, str) for v in [event.kind, event.slot, event.call_id, event.name]):
            raise ValueError("event identity fields must be strings")
        return event


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    attempt: int = 0
    event: int = -1


@dataclass
class Result:
    scenario: Scenario
    target: str
    events: list[Event]
    findings: list[Finding]
    duration: float = 0
    evidence: str = "wire"
    metadata: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.findings

    def summary(self) -> dict:
        return {
            "scenario": self.scenario.name,
            "target": self.target,
            "passed": self.passed,
            "duration_seconds": round(self.duration, 6),
            "evidence": self.evidence,
            "findings": [asdict(f) for f in self.findings],
            "metadata": self.metadata,
        }

    def trace(self) -> dict:
        return {
            "schema_version": 1,
            "scenario": asdict(self.scenario),
            "target": self.target,
            "evidence": self.evidence,
            "events": [asdict(e) for e in self.events],
        }

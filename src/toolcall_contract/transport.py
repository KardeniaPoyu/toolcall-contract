"""Closeable event sources. Optional SDKs have automatic retries disabled."""

import importlib.metadata
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from .fixtures import encode_sse, wire_events
from .model import Event, Scenario, canonical
from .protocols import PROTOCOLS, Normalizer
from .sse import Frame, SSEDecoder


@dataclass(frozen=True)
class Target:
    name: str
    protocol: str = "chat"
    transport: str = "http"
    base_url: str = ""
    model: str = "fixture"
    api_key_env: str = ""
    timeout: float = 15
    deadline: float = 60
    max_bytes: int = 8_388_608

    def validate(self):
        if any(
            not isinstance(v, str)
            for v in [
                self.name,
                self.protocol,
                self.transport,
                self.base_url,
                self.model,
                self.api_key_env,
            ]
        ):
            raise ValueError("target identity fields must be strings")
        if (
            not self.name
            or self.protocol not in PROTOCOLS
            or self.transport not in {"http", "sdk", "fixture"}
        ):
            raise ValueError("invalid target name, protocol, or transport")
        if (
            any(
                type(v) not in {int, float} or not math.isfinite(v) or v <= 0
                for v in [self.timeout, self.deadline]
            )
            or type(self.max_bytes) is not int
            or self.max_bytes < 1
        ):
            raise ValueError("target limits must be positive")
        if self.transport != "fixture":
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "base_url must be an HTTP(S) API root without credentials/query/fragment"
                )
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("non-loopback endpoints require HTTPS")
            if not self.model:
                raise ValueError("target requires a model")
            if self.api_key_env and not os.environ.get(self.api_key_env):
                raise ValueError("configured API key environment variable is empty")


def schema(value):
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: schema(v) for k, v in value.items()},
            "required": list(value),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        return {"type": "array", "items": {}}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    return {"type": "number" if isinstance(value, float) else "string"}


def request_body(target: Target, scenario: Scenario) -> dict:
    expected = [{"name": c.name, "arguments": c.arguments} for c in scenario.calls]
    prompt = (
        "Protocol conformance test. Call each tool listed below exactly once with the exact "
        "JSON arguments. Preserve every field, Unicode character, and type. Make all calls "
        "in this response. Do not emit explanatory text.\n" + canonical(expected)
    )
    definitions = {
        c.name: {
            "name": c.name,
            "description": "Record a conformance test value.",
            "parameters": schema(c.arguments),
        }
        for c in scenario.calls
    }
    body = {"model": target.model, "stream": True}
    if target.protocol == "responses":
        body.update(
            input=prompt,
            tools=[{"type": "function", **d, "strict": False} for d in definitions.values()],
            tool_choice="required",
        )
    elif target.protocol == "chat":
        body.update(
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "function", "function": d} for d in definitions.values()],
            tool_choice="required",
        )
    else:
        body.update(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            tools=[
                {
                    "name": d["name"],
                    "description": d["description"],
                    "input_schema": d["parameters"],
                }
                for d in definitions.values()
            ],
            tool_choice={"type": "any"},
        )
    return body


class FixtureSource:
    evidence = "wire-fixture"
    metadata = {}

    def __init__(self, target: Target, scenario: Scenario, mutation=None, chunk_size=13):
        self.protocol = target.protocol
        self.raw = encode_sse(wire_events(target.protocol, scenario, mutation), target.protocol)
        self.chunk_size = chunk_size
        self.closed = False

    def __iter__(self):
        decoder, normalizer = SSEDecoder(), Normalizer(self.protocol)
        for i in range(0, len(self.raw), self.chunk_size):
            if self.closed:
                return
            for frame in decoder.feed(self.raw[i : i + self.chunk_size]):
                yield from normalizer.feed(frame)
        for frame in decoder.feed(b"", final=True):
            yield from normalizer.feed(frame)

    def close(self):
        self.closed = True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API redirects are not followed")


class HTTPSource:
    evidence = "wire"
    metadata = {"transport": "stdlib-urllib"}

    def __init__(self, target: Target, scenario: Scenario):
        self.target, self.scenario = target, scenario
        self.response = None

    def __iter__(self):
        t = self.target
        path = {"chat": "/chat/completions", "responses": "/responses", "anthropic": "/messages"}[
            t.protocol
        ]
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        key = os.environ.get(t.api_key_env, "") if t.api_key_env else ""
        if t.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if key:
                headers["x-api-key"] = key
        elif key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(
            t.base_url.rstrip("/") + path,
            canonical(request_body(t, self.scenario)).encode(),
            headers,
        )
        started = time.monotonic()
        try:
            self.response = urllib.request.build_opener(NoRedirect()).open(req, timeout=t.timeout)
        except urllib.error.HTTPError as error:
            error.close()
            raise
        if self.response.headers.get_content_type() != "text/event-stream":
            raise ValueError("endpoint did not return text/event-stream")
        decoder, normalizer, received = SSEDecoder(), Normalizer(t.protocol), 0
        while True:
            if time.monotonic() - started > t.deadline:
                raise TimeoutError("attempt deadline exceeded")
            chunk = self.response.read1(4096)
            received += len(chunk)
            if received > t.max_bytes:
                raise ValueError("response exceeds configured limit")
            for frame in decoder.feed(chunk, final=not chunk):
                yield from normalizer.feed(frame)
            if not chunk:
                break

    def close(self):
        if self.response is not None:
            self.response.close()


class SDKSource:
    def __init__(self, target: Target, scenario: Scenario):
        self.target, self.scenario = target, scenario
        self.client = self.stream = None
        self.evidence = "sdk-eof" if target.protocol == "chat" else "sdk-events"
        package = "anthropic" if target.protocol == "anthropic" else "openai"
        self.metadata = {
            "sdk": package,
            "sdk_version": importlib.metadata.version(package),
            "sdk_max_retries": 0,
        }

    def __iter__(self):
        t = self.target
        key = os.environ.get(t.api_key_env, "") if t.api_key_env else "local-fixture"
        kwargs = dict(api_key=key, base_url=t.base_url, timeout=t.timeout, max_retries=0)
        # Do not let an endpoint redirect this credential-bearing request to another origin.
        import httpx

        kwargs["http_client"] = httpx.Client(follow_redirects=False, timeout=t.timeout)
        if t.protocol == "anthropic":
            import anthropic

            self.client = anthropic.Anthropic(**kwargs)
            self.stream = self.client.messages.create(**request_body(t, self.scenario))
        else:
            import openai

            self.client = openai.OpenAI(**kwargs)
            api = (
                self.client.responses if t.protocol == "responses" else self.client.chat.completions
            )
            self.stream = api.create(**request_body(t, self.scenario))
        normalizer, started, received = Normalizer(t.protocol), time.monotonic(), 0
        for item in self.stream:
            if time.monotonic() - started > t.deadline:
                raise TimeoutError("attempt deadline exceeded")
            raw = canonical(item.model_dump(exclude_none=True))
            received += len(raw.encode("utf-8"))
            if received > t.max_bytes:
                raise ValueError("response exceeds configured limit")
            yield from normalizer.feed(Frame("message", raw))
        if t.protocol == "chat":
            # SDK iterator exhaustion does NOT prove the wire contained [DONE].
            yield Event("stream_end", data="sdk_eof")

    def close(self):
        try:
            if self.stream is not None:
                self.stream.close()
        finally:
            if self.client is not None:
                self.client.close()


def source_for(target: Target, scenario: Scenario):
    return {"fixture": FixtureSource, "http": HTTPSource, "sdk": SDKSource}[target.transport](
        target, scenario
    )

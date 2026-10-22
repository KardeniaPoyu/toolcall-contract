import importlib.util

import pytest

from toolcall_contract.protocols import PROTOCOLS
from toolcall_contract.runner import run
from toolcall_contract.scenarios import suite
from toolcall_contract.server import fixture_server
from toolcall_contract.transport import Target


@pytest.fixture(scope="module")
def server():
    with fixture_server() as value:
        yield value


@pytest.mark.parametrize("transport", ["http", "sdk"])
@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("scenario", suite(), ids=lambda s: s.name)
def test_real_clients(server, transport, protocol, scenario):
    if transport == "sdk":
        pytest.importorskip("anthropic" if protocol == "anthropic" else "openai")
    base = f"http://127.0.0.1:{server.server_port}/v1"
    target = Target("primary", protocol, transport, base)
    fallback = Target("secondary", protocol, transport, base)
    result = run(scenario, target, fallback)
    assert result.passed, result.findings


@pytest.mark.parametrize(
    "mutation", ["missing-terminal", "argument-loss", "duplicate-call", "disconnect"]
)
@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_http_faults(mutation, protocol):
    with fixture_server(mutation=mutation) as server:
        target = Target("fault", protocol, "http", f"http://127.0.0.1:{server.server_port}/v1")
        result = run(suite()[0], target)
        assert not result.passed


@pytest.mark.parametrize("transport", ["http", "sdk"])
def test_no_hidden_429_retries(transport):
    if transport == "sdk":
        pytest.importorskip("openai")
    with fixture_server(mutation="http-429") as server:
        target = Target(
            "rate-limited", "chat", transport, f"http://127.0.0.1:{server.server_port}/v1"
        )
        result = run(suite()[0], target)
        assert not result.passed
        assert server.requests == 1


@pytest.mark.skipif(importlib.util.find_spec("openai") is None, reason="optional SDK")
def test_sdk_eof_is_explicitly_weaker_than_wire():
    with fixture_server(mutation="missing-terminal") as server:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        wire = run(suite()[0], Target("wire", "chat", "http", base))
        sdk = run(suite()[0], Target("sdk", "chat", "sdk", base))
        assert not wire.passed
        assert sdk.evidence == "sdk-eof"
        assert sdk.passed  # iterator exhaustion hides the missing sentinel

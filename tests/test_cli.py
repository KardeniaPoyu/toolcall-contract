import json
import xml.etree.ElementTree as ET
from dataclasses import asdict

import pytest

from toolcall_contract.cli import main
from toolcall_contract.transport import Target


def test_cli_reports_and_replay(tmp_path):
    assert main(["demo", "--report-dir", str(tmp_path), "--save-traces"]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["total"] == 27
    assert report["failed"] == 0
    assert len(ET.parse(tmp_path / "junit.xml").findall("testcase")) == 27
    assert "payload" not in (tmp_path / "report.json").read_text()
    trace = next(tmp_path.glob("*.trace.json"))
    assert main(["replay", str(trace), "--report-dir", str(tmp_path / "replay")]) == 0


def test_mutations_exit_nonzero(tmp_path):
    assert (
        main(
            [
                "demo",
                "--mutation",
                "argument-loss",
                "--scenario",
                "basic",
                "--report-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    assert json.loads((tmp_path / "report.json").read_text())["failed"] == 3


def test_config_validation_and_fixture_probe(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "targets": [asdict(Target("x", transport="fixture"))],
                "scenarios": ["basic"],
            }
        )
    )
    assert main(["probe", "--config", str(path), "--report-dir", str(tmp_path / "out")]) == 0
    path.write_text('{"schema_version":1,"targets":[]}')
    assert main(["probe", "--config", str(path)]) == 2


def test_no_false_green_on_empty_selection(tmp_path):
    assert (
        main(
            [
                "demo",
                "--protocol",
                "chat",
                "--mutation",
                "snapshot-mismatch",
                "--report-dir",
                str(tmp_path),
            ]
        )
        == 2
    )


def test_missing_file_is_configuration_error(tmp_path):
    assert main(["replay", str(tmp_path / "absent.json")]) == 2


@pytest.mark.parametrize(
    "data",
    [
        [],
        None,
        {"schema_version": 1, "targets": {}},
        {"schema_version": 1, "targets": [{"name": []}]},
        {"schema_version": 1, "targets": [{"name": "a", "timeout": "30"}]},
        {"schema_version": 1, "targets": [{"name": "a", "timeout": float("nan")}]},
        {
            "schema_version": 1,
            "targets": [{"name": "a", "transport": "fixture"}],
            "scenarios": "basic",
        },
    ],
)
def test_malformed_config_is_exit_two(tmp_path, data):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    assert main(["probe", "--config", str(path)]) == 2


@pytest.mark.parametrize(
    "base",
    [
        "http://example.com/v1",
        "https://user:pass@example.com/v1",
        "file:///tmp/test",
        "https://example.com/v1?key=secret",
    ],
)
def test_unsafe_endpoint_config_rejected(base):
    with pytest.raises(ValueError):
        Target("bad", base_url=base).validate()


def test_invalid_event_shape_returns_exit_two(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "bad",
                "scenario": {"name": "bad", "calls": [{"name": "x", "arguments": {}}]},
                "events": [{"kind": "attempt_start", "attempt": []}],
            }
        )
    )
    assert main(["replay", str(path)]) == 2

"""Portable CI reports. Summary reports intentionally omit argument and response payloads."""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__
from .model import Result


def write_reports(results: list[Result], directory: str | Path, traces: bool = False):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    failed = sum(not r.passed for r in results)
    report = {
        "schema_version": 1,
        "tool_version": __version__,
        "total": len(results),
        "passed": len(results) - failed,
        "failed": failed,
        "results": [r.summary() for r in results],
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    xml = ET.Element(
        "testsuite",
        name="toolcall-contract",
        tests=str(len(results)),
        failures=str(failed),
        errors="0",
        time=str(sum(r.duration for r in results)),
    )
    rows = [
        "# Toolcall Contract",
        "",
        f"**{len(results) - failed}/{len(results)} passed**",
        "",
        "| Target | Scenario | Result | Evidence | Findings |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(results):
        case = ET.SubElement(
            xml, "testcase", classname=r.target, name=r.scenario.name, time=str(r.duration)
        )
        properties = ET.SubElement(case, "properties")
        ET.SubElement(properties, "property", name="evidence", value=r.evidence)
        if r.findings:
            failure = ET.SubElement(
                case, "failure", message=", ".join(sorted({f.code for f in r.findings}))
            )
            failure.text = "\n".join(
                f"{f.code} attempt={f.attempt} event={f.event}: {f.message}" for f in r.findings
            )

        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

        rows.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    [
                        r.target,
                        r.scenario.name,
                        "PASS" if r.passed else "FAIL",
                        r.evidence,
                        ", ".join(sorted({f.code for f in r.findings})),
                    ],
                )
            )
            + " |"
        )
        if traces:
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{i:03d}-{r.target}-{r.scenario.name}")
            (root / (name + ".trace.json")).write_text(
                json.dumps(r.trace(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
    ET.indent(xml)
    ET.ElementTree(xml).write(root / "junit.xml", encoding="utf-8", xml_declaration=True)
    (root / "summary.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .fixtures import MUTATIONS
from .model import Event, Finding, Result, Scenario, strict_json
from .oracle import audit
from .protocols import PROTOCOLS
from .report import write_reports
from .runner import run
from .scenarios import suite
from .server import fixture_server
from .transport import FixtureSource, Target


def load_config(path):
    data = strict_json(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        raise ValueError("config must be an object with targets array")
    if data.get("schema_version") != 1 or set(data) - {
        "schema_version",
        "targets",
        "fallbacks",
        "scenarios",
    }:
        raise ValueError("unsupported config schema or unknown config field")
    targets = [Target(**t) for t in data["targets"]]
    if not targets or len({t.name for t in targets}) != len(targets):
        raise ValueError("targets must be nonempty with unique names")
    for target in targets:
        target.validate()
    names = {t.name: t for t in targets}
    fallbacks = data.get("fallbacks", {})
    if not isinstance(fallbacks, dict):
        raise ValueError("fallbacks must be an object")
    for source, dest in fallbacks.items():
        if source not in names or dest not in names or source == dest:
            raise ValueError("fallback references must name distinct configured targets")
    selected = data.get("scenarios", [s.name for s in suite()])
    if not isinstance(selected, list):
        raise ValueError("scenarios must be an array")
    if (
        not selected
        or len(selected) != len(set(selected))
        or set(selected) - {s.name for s in suite()}
    ):
        raise ValueError("scenarios must be a nonempty list of unique supported names")
    cases = [s for s in suite() if s.name in selected]
    if any(s.mode == "fallback" for s in cases) and any(t.name not in fallbacks for t in targets):
        raise ValueError(
            "each target needs a fallback mapping when fallback scenarios are selected"
        )
    return targets, names, fallbacks, cases


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tcc", description="Tool-call protocol acceptance suite")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list semantic scenarios")
    local = commands.add_parser(
        "local", help="test actual HTTP/SDK clients against a local SSE server"
    )
    local.add_argument(
        "--sdk", action="store_true", help="also test installed OpenAI and Anthropic SDKs"
    )
    serve = commands.add_parser(
        "serve", help="serve synthetic SSE on loopback for external SDK tests"
    )
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--mutation", choices=[*MUTATIONS, "http-429", "disconnect"])
    serve.add_argument("--fragment-bytes", type=int, default=17)
    serve.add_argument("--delay", type=float, default=0)
    demo = commands.add_parser("demo", help="run deterministic wire fixtures offline")
    demo.add_argument("--protocol", choices=["all", *PROTOCOLS], default="all")
    demo.add_argument("--mutation", choices=MUTATIONS)
    demo.add_argument("--scenario", choices=[s.name for s in suite()])
    probe = commands.add_parser("probe", help="probe configured endpoints (may incur API charges)")
    probe.add_argument("--config", required=True)
    probe.add_argument(
        "--require-wire", action="store_true", help="fail if a target hides wire termination"
    )
    replay = commands.add_parser(
        "replay", help="audit canonical trace files from any SDK or language"
    )
    replay.add_argument("files", nargs="+")
    for sub in (demo, probe, replay, local):
        sub.add_argument("--report-dir", default="reports")
        sub.add_argument(
            "--save-traces",
            action="store_true",
            help="save raw tool arguments; synthetic inputs recommended",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            for s in suite():
                print(f"{s.name:28} {s.mode}")
            return 0
        if args.command == "serve":
            with fixture_server(
                args.port, args.mutation, args.fragment_bytes, args.delay
            ) as server:
                print(f"Synthetic SSE server: http://127.0.0.1:{server.server_port}/v1", flush=True)
                while True:
                    time.sleep(0.25)
        results = []
        if args.command == "local":
            with fixture_server() as server:
                base = f"http://127.0.0.1:{server.server_port}/v1"
                for transport in ["http", "sdk"] if args.sdk else ["http"]:
                    for protocol in PROTOCOLS:
                        target = Target(f"{transport}-{protocol}", protocol, transport, base)
                        fallback = Target(
                            f"{transport}-{protocol}-fallback", protocol, transport, base
                        )
                        for scenario in suite():
                            results.append(run(scenario, target, fallback))
        elif args.command == "demo":
            protocols = PROTOCOLS if args.protocol == "all" else [args.protocol]
            for protocol in protocols:
                target = Target(protocol, protocol, "fixture")
                fallback = Target(protocol + "-fallback", protocol, "fixture")
                for scenario in suite():
                    if args.scenario and scenario.name != args.scenario:
                        continue
                    if args.mutation == "snapshot-mismatch" and protocol != "responses":
                        continue
                    results.append(
                        run(
                            scenario,
                            target,
                            fallback,
                            factory=lambda t, s: FixtureSource(t, s, args.mutation),
                        )
                    )
        elif args.command == "probe":
            targets, names, fallbacks, scenarios = load_config(args.config)
            for target in targets:
                for scenario in scenarios:
                    result = run(scenario, target, names.get(fallbacks.get(target.name)))
                    if args.require_wire and result.evidence not in {"wire", "wire-fixture"}:
                        result.findings.append(
                            Finding(
                                "INSUFFICIENT_EVIDENCE", "This CI gate requires raw wire evidence"
                            )
                        )
                    results.append(result)
        else:
            for file in args.files:
                data = strict_json(Path(file).read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schema_version") != 1:
                    raise ValueError("unsupported trace schema")
                if not isinstance(data.get("target"), str) or not isinstance(
                    data.get("events"), list
                ):
                    raise ValueError("invalid trace target/events")
                scenario = Scenario.from_dict(data["scenario"])
                events = [Event.from_dict(e) for e in data["events"]]
                results.append(
                    Result(
                        scenario,
                        data["target"],
                        events,
                        audit(scenario, events),
                        evidence=data.get("evidence", "external"),
                    )
                )
        if not results:
            raise ValueError("selection produced zero tests")
        write_reports(results, args.report_dir, args.save_traces)
        for result in results:
            codes = ", ".join(sorted({f.code for f in result.findings}))
            print(
                f"{'PASS' if result.passed else 'FAIL'} {result.target}/{result.scenario.name} {codes}"
            )
        print(f"{sum(r.passed for r in results)}/{len(results)} passed; reports: {args.report_dir}")
        return 0 if all(r.passed for r in results) else 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Do not print values from malformed config/trace files or server responses.
        print(
            f"Configuration/input error ({type(exc).__name__}); check schema, files and environment.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 130

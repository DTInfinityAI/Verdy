"""Command-line interface.

Exit codes for ``verdy run``: 0 PASS, 1 FAIL, 3 INCONCLUSIVE. Every command exits with 2
on invalid input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from verdy import __version__

EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3}


def _err(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def cmd_validate(args: argparse.Namespace) -> int:
    from verdy.metrics.stl import SpecError, parse_specs
    from verdy.odd import check_odd, read_document

    status = 0
    for path in args.files:
        try:
            doc = read_document(path)
        except Exception as exc:
            print(f"{path}: cannot read: {exc}")
            status = 2
            continue
        if "specs" in doc:
            try:
                specs = parse_specs(doc)
                print(f"{path}: OK ({len(specs)} STL specs)")
            except SpecError as exc:
                print(f"{path}: INVALID\n  - {exc}")
                status = 2
            continue
        report = check_odd(doc, strict=args.strict)
        for w in report.warnings:
            print(f"{path}: warning: {w}")
        if report.ok:
            print(f"{path}: OK (ODD with {len(doc['parameters'])} parameters)")
        else:
            print(f"{path}: INVALID")
            for e in report.errors:
                print(f"  - {e}")
            status = 2
    return status


def cmd_sample(args: argparse.Namespace) -> int:
    from verdy.odd import load_odd
    from verdy.sampler import make_sampler

    odd = load_odd(args.odd)
    sampler = make_sampler(args.sampler, odd, seed=args.seed)
    scenarios = [s.to_dict() for s in sampler.sample(args.n)]
    if args.json:
        print(json.dumps(scenarios, indent=2))
        return 0
    names = odd.parameter_names
    widths = {n: max(len(n), 8) for n in names}
    print("id      " + "  ".join(n.ljust(widths[n]) for n in names))
    for s in scenarios:
        cells = []
        for n in names:
            v = s["params"][n]
            text = f"{v:.3g}" if isinstance(v, float) else str(v)
            cells.append(text.ljust(widths[n]))
        print(f"{s['id']:<8}" + "  ".join(cells))
    return 0


def _signing_key(args: argparse.Namespace) -> Any:
    if args.sign == "hmac":
        key = os.environ.get(args.key_env)
        if not key:
            raise ValueError(f"set {args.key_env} to the HMAC secret")
        return key
    if not args.key:
        raise ValueError("--key PATH to an Ed25519 private key PEM is required")
    return Path(args.key)


def cmd_run(args: argparse.Namespace) -> int:
    from verdy.config import load_run_config
    from verdy.harness import evaluate
    from verdy.ledger import sign_report, write_report

    cfg = load_run_config(args.config, runs=args.runs, seed=args.seed)
    if args.max_failure_prob is not None:
        cfg.verdict.max_failure_prob = args.max_failure_prob

    def progress(done: int, total: int) -> None:
        if not args.quiet and (done % 25 == 0 or done == total):
            print(f"\r  {done}/{total} runs", end="", file=sys.stderr, flush=True)

    keep = "failures" if args.traces else cfg.keep_traces
    result = evaluate(
        cfg.odd, cfg.specs, cfg.backend, cfg.policy, cfg.sampler, cfg.runs, cfg.verdict,
        batch_size=cfg.batch_size, keep_traces=keep,
        inputs={"config": cfg.raw, "file_sha256": cfg.file_hashes},
        progress=progress,
    )
    if not args.quiet:
        print(file=sys.stderr)
    report = result.report
    if args.sign:
        sign_report(report, "hmac-sha256" if args.sign == "hmac" else "ed25519",
                    _signing_key(args))
    output = Path(args.output) if args.output else cfg.output
    write_report(report, output)
    if result.traces:
        trace_dir = output.parent / (output.name.split(".")[0] + "_traces")
        trace_dir.mkdir(parents=True, exist_ok=True)
        for run_id, trace in result.traces.items():
            (trace_dir / f"{run_id}.json").write_text(json.dumps(trace), "utf-8")
        print(f"Saved {len(result.traces)} traces to {trace_dir}")
    print(result.summary())
    print(f"Report written to {output}")
    return EXIT_CODES[result.status]


def cmd_verify(args: argparse.Namespace) -> int:
    from verdy.ledger import IntegrityError, SignatureError, read_report, verify_report

    report = read_report(args.report)
    key: Any = None
    if args.key_env:
        key = os.environ.get(args.key_env)
        if not key:
            return _err(f"{args.key_env} is not set")
    elif args.public_key:
        key = Path(args.public_key)
    try:
        info = verify_report(report, key)
    except (IntegrityError, SignatureError) as exc:
        print(f"{args.report}: NOT VERIFIED: {exc}")
        return 1
    verdict = report["results"]["verdict"]["status"]
    sig = (f"signature OK ({info['signature_method']})" if info["signature_checked"]
           else "signature not checked")
    print(f"{args.report}: digest OK, {sig}; verdict {verdict}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from verdy.verdict import required_runs

    n = required_runs(args.max_failure_prob, args.confidence)
    print(
        f"{n} failure-free runs are needed to show failure probability <= "
        f"{args.max_failure_prob:g} with {args.confidence:.0%} confidence."
    )
    return 0


def cmd_author(args: argparse.Namespace) -> int:
    from verdy.odd import save_odd
    from verdy.odd.authoring import draft_odd

    description = Path(args.from_file).read_text("utf-8") if args.from_file else args.description
    if not description:
        return _err("give a description or --from-file")
    odd = draft_odd(description, model=args.model)
    save_odd(odd, args.output)
    print(f"Draft ODD with {len(odd.parameters)} parameters written to {args.output}.")
    print("Review every parameter, then set provenance.approved: true on the ones you accept.")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from verdy.spec import load_schema

    print(json.dumps(load_schema(args.name), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verdy", description="A driving test for robot AI.")
    parser.add_argument("--version", action="version", version=f"verdy {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate ODD and STL spec files")
    p.add_argument("files", nargs="+")
    p.add_argument("--strict", action="store_true",
                   help="treat unapproved LLM-authored parameters as errors")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("sample", help="print scenarios sampled from an ODD")
    p.add_argument("odd")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--sampler", default="stratified",
                   choices=["monte_carlo", "stratified", "importance"])
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("run", help="run an evaluation from a run config")
    p.add_argument("config")
    p.add_argument("--runs", type=int, help="override the number of runs")
    p.add_argument("--seed", type=int, help="override the seed")
    p.add_argument("--max-failure-prob", type=float, help="override verdict.max_failure_prob")
    p.add_argument("-o", "--output", help="report path (default: config 'output')")
    p.add_argument("--traces", action="store_true", help="save traces of failed runs")
    p.add_argument("--sign", choices=["hmac", "ed25519"], help="sign the report")
    p.add_argument("--key-env", default="VERDY_SIGNING_KEY",
                   help="env var holding the HMAC secret (default VERDY_SIGNING_KEY)")
    p.add_argument("--key", help="Ed25519 private key PEM")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("verify", help="check a report's digest and signature")
    p.add_argument("report")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--key-env", help="env var holding the HMAC secret")
    group.add_argument("--public-key", help="Ed25519 public key PEM")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("plan", help="runs needed to demonstrate a failure-probability target")
    p.add_argument("--max-failure-prob", type=float, required=True)
    p.add_argument("--confidence", type=float, default=0.95)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("author", help="draft an ODD from a description with Claude")
    p.add_argument("description", nargs="?")
    p.add_argument("--from-file", help="read the description from a file")
    p.add_argument("-o", "--output", default="odd.draft.yaml")
    p.add_argument("--model", default="claude-opus-5-5")
    p.set_defaults(func=cmd_author)

    p = sub.add_parser("schema", help="print a bundled JSON Schema")
    p.add_argument("name", choices=["odd", "stl_specs"])
    p.set_defaults(func=cmd_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, ImportError) as exc:
        return _err(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

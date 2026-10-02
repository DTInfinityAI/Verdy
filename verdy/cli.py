"""Command-line interface.

Exit codes for ``verdy run`` and ``verdy improve`` (final certified verdict): 0 PASS,
1 FAIL, 3 INCONCLUSIVE. Every command exits with 2
on invalid input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from verdy import __version__

EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3}


DEFAULT_SECRET_NAMES = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "VERDY_SIGNING_KEY",
    "VERDY_FINGERPRINT_KEY",
]


def _err(message: str) -> int:
    from verdy.secrets import redact

    print(f"error: {redact(message)}", file=sys.stderr)
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
    from verdy.secrets import get_secret

    if args.sign == "hmac":
        return get_secret(args.key_env).reveal()
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
    inputs: dict[str, Any] = {"config": cfg.raw, "file_sha256": cfg.file_hashes}
    for key in ("policy_name", "policy_version"):
        value = getattr(args, key) or cfg.raw.get(key)
        if value:
            inputs[key] = value
    store = None
    store_path = args.store or (
        str(cfg.path.parent / cfg.raw["store"]) if cfg.raw.get("store") else None)
    if store_path:
        from verdy.store import open_store

        store = open_store(store_path)
    result = evaluate(
        cfg.odd, cfg.specs, cfg.backend, cfg.policy, cfg.sampler, cfg.runs, cfg.verdict,
        batch_size=cfg.batch_size, keep_traces=keep, inputs=inputs,
        progress=progress, fingerprint=cfg.fingerprint,
        trace_sink=store.put_trace if store else None,
    )
    if store is not None:
        store.flush()  # traces are on disk before the report that points at them
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
    if store is not None:
        with store:
            store.index([output])
        print(f"Traces stored and report indexed in {store_path}")
    return EXIT_CODES[result.status]


def cmd_index(args: argparse.Namespace) -> int:
    from verdy.store import open_store

    with open_store(args.store) as store:
        paths = args.paths or (["."] if not args.rebuild else [])
        result = store.rebuild(paths) if args.rebuild else store.index(paths)
        for path, reason in result.skipped:
            print(f"skipped {path}: {reason}")
        print(f"{'Rebuilt index' if args.rebuild else 'Indexed'}: {len(result.indexed)} "
              f"report(s) in {args.store}")
    return 0


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def cmd_store_stats(args: argparse.Namespace) -> int:
    from verdy.store import ParquetTraceStore

    st = ParquetTraceStore(args.store).stats()
    print(f"Store {args.store}: {st.traces} traces, {_size(st.bytes_on_disk)}")
    print(f"  batched:     {st.batched_traces} traces in {st.batch_files} batch file(s)")
    print(f"  single-file: {st.single_files} trace(s)"
          + ("  (run `verdy store compact` to batch them)" if st.single_files else ""))
    return 0


def cmd_store_compact(args: argparse.Namespace) -> int:
    from verdy.store import ParquetTraceStore

    store = ParquetTraceStore(args.store, batch_size=args.batch_size)
    before = store.stats().bytes_on_disk
    traces, batches = store.compact(delete=not args.keep)
    after = store.stats().bytes_on_disk
    print(f"Packed {traces} single-file trace(s) into {batches} batch file(s); "
          f"{_size(before)} -> {_size(after)}")
    return 0


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.2f}%"


def cmd_history(args: argparse.Namespace) -> int:
    from verdy.store import open_store

    with open_store(args.store) as store:
        if not args.policy:
            rows = store.policies()
            if not rows:
                print("No reports indexed yet. Run `verdy index` or `verdy run --store`.")
                return 0
            print(f"{'policy':<40} {'versions':>8} {'reports':>8}")
            for name, versions, reports in rows:
                print(f"{name:<40} {versions:>8} {reports:>8}")
            return 0
        history = store.history(args.policy)
    if not history:
        print(f"No indexed reports for policy {args.policy!r}.")
        return 0
    if args.json:
        print(json.dumps([h.__dict__ for h in history], indent=2, default=str))
        return 0
    regressions = 0
    for name, suite in dict.fromkeys((h.policy, h.suite) for h in history):
        rows = [h for h in history if h.policy == name and h.suite == suite]
        specs = sorted({s for h in rows for s in h.per_spec}) if args.by_spec else []
        print(f"Policy {name}\n  Suite: {suite}")
        header = (f"  {'version':<14} {'date':<19} {'verdict':<12} {'p_fail':>8} "
                  f"{'bounds':>19} {'runs':>6}")
        header += "".join(f" {s[:16]:>16}" for s in specs)
        print(header)
        for h in rows:
            bounds = f"[{_pct(h.p_lower)}, {_pct(h.p_upper)}]"
            line = (f"  {h.version[:14]:<14} {h.created_at[:19]:<19} {h.status:<12} "
                    f"{_pct(h.p_fail):>8} {bounds:>19} {h.n_runs:>6}")
            line += "".join(f" {_pct(h.per_spec.get(s)):>16}" for s in specs)
            print(line)
            if h.regression:
                regressions += 1
                print(f"  {'':<14} ^ REGRESSION: {h.regression}")
        print()
    if regressions:
        print(f"{regressions} regression(s) found.")
    return 1 if regressions and args.fail_on_regression else 0


def cmd_query(args: argparse.Namespace) -> int:
    from verdy.store import open_store

    with open_store(args.store) as store:
        columns, rows = store.query(args.sql)
    if args.json:
        print(json.dumps([dict(zip(columns, r, strict=True)) for r in rows], indent=2,
                         default=str))
        return 0
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(str(c))
              for i, c in enumerate(columns)]
    print("  ".join(str(c).ljust(w) for c, w in zip(columns, widths, strict=True)))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)))
    return 0


def cmd_improve(args: argparse.Namespace) -> int:
    from verdy.improve.config import load_loop_config
    from verdy.ledger import sign_report, write_report

    cfg = load_loop_config(args.config, cycles=args.cycles)
    if not args.quiet:
        cfg.loop.progress = lambda msg: print(f"  {msg}", file=sys.stderr, flush=True)
    result = cfg.loop.run(cfg.cycles)
    if args.sign:
        sign_report(result.report, "hmac-sha256" if args.sign == "hmac" else "ed25519",
                    _signing_key(args))
        write_report(result.report, result.report_path)
    print(result.summary())
    return EXIT_CODES[result.final.status]


def cmd_verify(args: argparse.Namespace) -> int:
    from verdy.ledger import IntegrityError, SignatureError, read_report, verify_report

    report = read_report(args.report)
    from verdy.secrets import get_secret

    key: Any = None
    if args.key_env:
        key = get_secret(args.key_env).reveal()
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


def cmd_secrets_status(args: argparse.Namespace) -> int:
    from verdy.secrets import describe_secrets, secrets_file_path

    names = args.names or DEFAULT_SECRET_NAMES
    print(f"Secrets file: {secrets_file_path()}"
          + ("" if secrets_file_path().is_file() else " (not present)"))
    for name, info in describe_secrets(names, args.fingerprint).items():
        if not info["set"]:
            print(f"  {name:<24} not set")
        else:
            fp = info["fingerprint"] or "(fingerprint disabled)"
            print(f"  {name:<24} set ({info['source']})  {fp}")
    return 0


def cmd_secrets_scan(args: argparse.Namespace) -> int:
    from verdy.secrets import scan_files

    hits = scan_files(args.paths)
    for path, line in hits:
        print(f"{path}:{line}: possible credential")
    if hits:
        print(f"{len(hits)} possible credential(s) found. Remove them and rotate the keys.")
        return 1
    print("No credentials found.")
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
                   help="secret holding the HMAC key (default VERDY_SIGNING_KEY)")
    p.add_argument("--key", help="Ed25519 private key PEM")
    p.add_argument("--store", nargs="?", const=".verdy/store", default=None,
                   help="store traces as Parquet and index the report (default path "
                        ".verdy/store); also settable as 'store' in the run config")
    p.add_argument("--policy-name", help="policy identity for history (overrides config)")
    p.add_argument("--policy-version",
                   help="policy version for history, e.g. a release tag or git commit")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("index", help="index evidence reports into the local store")
    p.add_argument("paths", nargs="*", help="report files or directories (default: .)")
    p.add_argument("--store", default=".verdy/store")
    p.add_argument("--rebuild", action="store_true",
                   help="drop the index and rebuild it from all known reports")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("store", help="inspect or compact the trace store")
    store_sub = p.add_subparsers(dest="store_command", required=True)
    q = store_sub.add_parser("stats", help="traces, files and size of the store")
    q.add_argument("--store", default=".verdy/store")
    q.set_defaults(func=cmd_store_stats)
    q = store_sub.add_parser("compact", help="pack single-file traces into batch files")
    q.add_argument("--store", default=".verdy/store")
    q.add_argument("--batch-size", type=int, default=1000, help="traces per batch file")
    q.add_argument("--keep", action="store_true", help="keep the single files")
    q.set_defaults(func=cmd_store_compact)

    p = sub.add_parser("history", help="verdicts of a policy across versions")
    p.add_argument("policy", nargs="?", help="policy name (substring match); omit to list")
    p.add_argument("--store", default=".verdy/store")
    p.add_argument("--by-spec", action="store_true", help="add per-spec violation rates")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fail-on-regression", action="store_true",
                   help="exit with 1 if any regression is found (for CI)")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("query", help="run a read-only SQL query on the evidence index")
    p.add_argument("sql")
    p.add_argument("--store", default=".verdy/store")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("improve", help="run the closed improvement loop from a loop config")
    p.add_argument("config")
    p.add_argument("--cycles", type=int, help="override the number of cycles")
    p.add_argument("--sign", choices=["hmac", "ed25519"], help="sign the loop report")
    p.add_argument("--key-env", default="VERDY_SIGNING_KEY",
                   help="secret holding the HMAC key (default VERDY_SIGNING_KEY)")
    p.add_argument("--key", help="Ed25519 private key PEM")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_improve)

    p = sub.add_parser("verify", help="check a report's digest and signature")
    p.add_argument("report")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--key-env", help="secret holding the HMAC key")
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

    p = sub.add_parser("secrets", help="check credentials without revealing them")
    secrets_sub = p.add_subparsers(dest="secrets_command", required=True)
    q = secrets_sub.add_parser("status", help="which secrets are set, with fingerprints")
    q.add_argument("names", nargs="*", help=f"default: {' '.join(DEFAULT_SECRET_NAMES)}")
    q.add_argument("--fingerprint", default="sha256", choices=["sha256", "hmac", "none"])
    q.set_defaults(func=cmd_secrets_status)
    q = secrets_sub.add_parser("scan", help="find credential-looking strings in files")
    q.add_argument("paths", nargs="+")
    q.set_defaults(func=cmd_secrets_scan)

    p = sub.add_parser("schema", help="print a bundled JSON Schema")
    p.add_argument("name", choices=["odd", "stl_specs"])
    p.set_defaults(func=cmd_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    import logging

    from verdy.secrets import RedactingFilter, SecretError

    args = build_parser().parse_args(argv)
    redacting = RedactingFilter()
    root = logging.getLogger()
    root.addFilter(redacting)
    for handler in root.handlers:
        handler.addFilter(redacting)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, ImportError, SecretError, RuntimeError,
            KeyError) as exc:
        return _err(str(exc))
    finally:
        root.removeFilter(redacting)
        for handler in root.handlers:
            handler.removeFilter(redacting)


if __name__ == "__main__":
    raise SystemExit(main())

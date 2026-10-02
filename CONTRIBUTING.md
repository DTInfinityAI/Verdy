# Contributing to Verdy

Thanks for helping make robot policies safer to deploy.

## Set up

```bash
git clone https://github.com/DTInfinityAI/Verdy.git
cd Verdy
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,sign]"
```

## Before opening a pull request

```bash
ruff check .
pytest
```

Both run in CI on Python 3.10, 3.11 and 3.12.

## Guidelines

- Keep changes focused, and add tests for new behaviour and bug fixes.
- Statistical changes need a test that checks the numbers against a known result or a
  large Monte Carlo baseline.
- Changes to `verdy/spec/*.schema.json` are format changes: update
  [`docs/odd-spec.md`](docs/odd-spec.md) or [`docs/stl-specs.md`](docs/stl-specs.md) and
  bump the spec version.
- Update the docs and [`CHANGELOG.md`](CHANGELOG.md) for user-visible changes.

## Reporting issues

Open a GitHub issue with the Verdy version (`verdy --version`), the command or code you
ran, and what you expected. For evaluation results, attach the report JSON if you can.

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).

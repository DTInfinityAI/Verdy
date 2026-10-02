"""Generate the sample logs in ./logs.

Real deployments would export these from robot bags or telemetry. Here they are recorded
from the built-in simulator so the example is self-contained.

    python make_logs.py --count 24
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "home_robot"))

from policy import CautiousNavigator  # noqa: E402

from verdy.backends import HomeNavSim  # noqa: E402
from verdy.odd import load_odd  # noqa: E402
from verdy.sampler import MonteCarloSampler  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--seed", type=int, default=2024)
    args = parser.parse_args()

    odd = load_odd(HERE.parent / "home_robot" / "odd.yaml")
    backend = HomeNavSim(dt=0.1, horizon=20.0)
    backend.bind(odd)
    out = HERE / "logs"
    out.mkdir(exist_ok=True)
    for scenario in MonteCarloSampler(odd, seed=args.seed).sample(args.count):
        env = backend.build(scenario.to_dict())
        trace = backend.rollout(env, CautiousNavigator(), scenario.seed)
        trace = {k: [round(v, 3) for v in vals] for k, vals in trace.items()}
        params = {k: round(v, 3) if isinstance(v, float) else v for k, v in scenario.params.items()}
        log = {"robot": "demo-unit-01", "params": params, "trace": trace}
        (out / f"run_{scenario.id}.json").write_text(json.dumps(log, separators=(",", ":")))
    print(f"wrote {args.count} logs to {out}")


if __name__ == "__main__":
    main()

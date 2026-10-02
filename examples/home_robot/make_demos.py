"""Record the demonstration file used by improve.yaml.

    python make_demos.py

Real demonstrations would be exported from teleoperation logs in the same JSONL format
(one step per line with "obs" and "action").
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from simulated_operator import expert_policy  # noqa: E402

from verdy.backends import HomeNavSim  # noqa: E402
from verdy.improve import record_demonstrations  # noqa: E402
from verdy.odd import load_odd  # noqa: E402
from verdy.sampler import MonteCarloSampler  # noqa: E402

if __name__ == "__main__":
    odd = load_odd(HERE / "odd.yaml")
    scenarios = MonteCarloSampler(odd, seed=4242).sample(6)
    n = record_demonstrations(odd, HomeNavSim(), expert_policy(), scenarios,
                              HERE / "demos" / "operator.jsonl", every=3)
    print(f"recorded {n} demonstration steps")

"""How much of the ODD the tested scenarios actually covered."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any

import numpy as np

from verdy.odd.model import ODD, Parameter


@dataclass
class CoverageReport:
    bins: int
    per_parameter: dict[str, float]
    uncovered: dict[str, list[str]]
    overall: float
    pairwise: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cells(param: Parameter, bins: int) -> list[str]:
    if param.is_numeric:
        lo, hi = param.range
        edges = np.linspace(lo, hi, bins + 1)
        return [f"[{edges[i]:g}, {edges[i + 1]:g}]" for i in range(bins)]
    return [repr(v) for v in param.domain]


def _cell_of(param: Parameter, value: Any, bins: int) -> int | None:
    if value is None:
        return None
    if param.is_numeric:
        lo, hi = param.range
        idx = int((float(value) - lo) / (hi - lo) * bins)
        return min(max(idx, 0), bins - 1)
    domain = param.domain
    return domain.index(value) if value in domain else None


def compute_coverage(
    odd: ODD, params: Sequence[dict[str, Any]], bins: int = 5, pairwise: bool = True
) -> CoverageReport:
    """Fraction of each parameter's cells hit by at least one scenario.

    Numeric parameters are split into ``bins`` equal-width cells over their range;
    categorical and boolean parameters have one cell per value. ``pairwise`` is the
    fraction of all two-parameter cell combinations that were hit.
    """
    per: dict[str, float] = {}
    uncovered: dict[str, list[str]] = {}
    hits: dict[str, list[int | None]] = {}
    for p in odd.parameters:
        cells = _cells(p, bins)
        idx = [_cell_of(p, row.get(p.name), bins) for row in params]
        hits[p.name] = idx
        seen = {i for i in idx if i is not None}
        per[p.name] = len(seen) / len(cells) if cells else 1.0
        missing = [cells[i] for i in range(len(cells)) if i not in seen]
        if missing:
            uncovered[p.name] = missing
    overall = float(np.mean(list(per.values()))) if per else 1.0

    pair_cov = None
    notes = []
    if pairwise and len(odd.parameters) > 1:
        total = covered = 0
        for a, b in combinations(odd.parameters, 2):
            na, nb = len(_cells(a, bins)), len(_cells(b, bins))
            seen_pairs = {
                (i, j) for i, j in zip(hits[a.name], hits[b.name], strict=True)
                if i is not None and j is not None
            }
            total += na * nb
            covered += len(seen_pairs)
        pair_cov = covered / total if total else 1.0
        if odd.constraints:
            notes.append("pairwise coverage counts cells excluded by ODD constraints as missed")
    return CoverageReport(bins, per, uncovered, overall, pair_cov, notes)

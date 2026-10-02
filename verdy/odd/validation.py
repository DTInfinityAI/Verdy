"""Structural (JSON Schema) and semantic validation of ODD documents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jsonschema

from verdy.odd.constraints import ConstraintError, compile_constraints
from verdy.odd.model import ODD
from verdy.spec import load_schema


class ODDValidationError(ValueError):
    """Raised when an ODD document is invalid."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("invalid ODD:\n  - " + "\n  - ".join(errors))
        self.errors = errors


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_odd(data: dict[str, Any], *, strict: bool = False) -> ValidationReport:
    """Validate an ODD document and return all errors and warnings.

    With ``strict=True``, LLM-authored parameters that a human has not approved are errors
    instead of warnings.
    """
    from verdy.sampler.distributions import DistributionError, distribution_for

    report = ValidationReport()
    validator = jsonschema.Draft202012Validator(load_schema("odd"))
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        report.errors.append(f"{where}: {err.message}")
    if report.errors:
        return report

    odd = ODD.from_dict(data)
    seen: set[str] = set()
    for p in odd.parameters:
        if p.name in seen:
            report.errors.append(f"parameters: duplicate parameter name {p.name!r}")
        seen.add(p.name)

        if p.is_numeric and p.values is not None:
            report.warnings.append(f"{p.name}: 'values' is ignored for {p.type} parameters")
        if not p.is_numeric and p.range is not None:
            report.warnings.append(f"{p.name}: 'range' is ignored for {p.type} parameters")
        if p.type == "categorical" and p.values and len(set(map(repr, p.values))) != len(p.values):
            report.errors.append(f"{p.name}: 'values' must be unique")
        try:
            distribution_for(p)
        except DistributionError as exc:
            report.errors.append(str(exc))
        if p.default is not None and not _in_domain(p, p.default):
            report.errors.append(f"{p.name}: default {p.default!r} is outside the domain")
        if not p.approved:
            msg = f"{p.name}: LLM-authored parameter has not been approved by a human"
            (report.errors if strict else report.warnings).append(msg)

    try:
        for c in compile_constraints(odd.constraints):
            unknown = c.names - seen
            if unknown:
                report.errors.append(
                    f"constraint {c.expression!r} references unknown parameters: "
                    + ", ".join(sorted(unknown))
                )
    except ConstraintError as exc:
        report.errors.append(str(exc))
    return report


def _in_domain(p: Any, value: Any) -> bool:
    if p.is_numeric:
        return isinstance(value, (int, float)) and p.range[0] <= value <= p.range[1]
    return value in p.domain


def validate_odd(data: dict[str, Any], *, strict: bool = False) -> ODD:
    """Validate an ODD document and return it as an :class:`ODD`, or raise."""
    report = check_odd(data, strict=strict)
    if not report.ok:
        raise ODDValidationError(report.errors)
    return ODD.from_dict(data)

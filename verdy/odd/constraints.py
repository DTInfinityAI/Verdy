"""Safe evaluation of ODD constraint expressions.

Constraints are small Python-syntax boolean expressions over parameter names, e.g.
``"obstacle_speed <= 1.5 or lighting_lux >= 100"``. Only literals, names, arithmetic,
comparisons, ``and``/``or``/``not``, ``in`` and a few math functions are allowed.
"""
from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping
from typing import Any

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_FUNCS = {"abs": abs, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log, "exp": math.exp}
_CONSTS = {"true": True, "false": False, "True": True, "False": False}


class ConstraintError(ValueError):
    """Raised when a constraint cannot be parsed or evaluated."""


class Constraint:
    def __init__(self, expression: str) -> None:
        self.expression = expression
        try:
            self._tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ConstraintError(f"invalid constraint {expression!r}: {exc.msg}") from exc
        self.names = _check(self._tree.body, expression)

    def __call__(self, values: Mapping[str, Any]) -> bool:
        return bool(_eval(self._tree.body, values, self.expression))

    def __repr__(self) -> str:
        return f"Constraint({self.expression!r})"


def _check(node: ast.AST, expr: str) -> set[str]:
    """Reject unsupported syntax and return referenced parameter names."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            if sub.id not in _CONSTS and sub.id not in _FUNCS:
                names.add(sub.id)
        elif isinstance(sub, ast.Call):
            if not isinstance(sub.func, ast.Name) or sub.func.id not in _FUNCS or sub.keywords:
                raise ConstraintError(f"unsupported function call in constraint {expr!r}")
        elif not isinstance(
            sub,
            (ast.Constant, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.List, ast.Tuple,
             ast.Load, ast.And, ast.Or, ast.cmpop, ast.operator, ast.unaryop, ast.IfExp),
        ):
            raise ConstraintError(
                f"unsupported syntax {type(sub).__name__} in constraint {expr!r}"
            )
    return names


def _eval(node: ast.AST, env: Mapping[str, Any], expr: str) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        if node.id not in env:
            raise ConstraintError(f"unknown parameter {node.id!r} in constraint {expr!r}")
        return env[node.id]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, env, expr) for v in node.values)
        return any(_eval(v, env, expr) for v in node.values)
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, env, expr), _eval(node.right, env, expr))
    if isinstance(node, ast.UnaryOp):
        return _UNARYOPS[type(node.op)](_eval(node.operand, env, expr))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env, expr)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _eval(comp, env, expr)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, env, expr) for e in node.elts]
    if isinstance(node, ast.IfExp):
        branch = node.body if _eval(node.test, env, expr) else node.orelse
        return _eval(branch, env, expr)
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](*(_eval(a, env, expr) for a in node.args))
    raise ConstraintError(f"unsupported syntax in constraint {expr!r}")


def compile_constraints(expressions: list[str]) -> list[Constraint]:
    return [Constraint(e) for e in expressions]


def satisfies(constraints: list[Constraint], values: Mapping[str, Any]) -> bool:
    return all(c(values) for c in constraints)

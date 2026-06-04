from __future__ import annotations

import operator
import re
from typing import Any

OPERATORS: dict[str, Any] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}

# Multi-char operators must appear before single-char ones so the regex
# matches >= before > and <= before <.
_OP_PATTERN = re.compile(
    r"payload\.(\w+)\s*(>=|<=|==|!=|>|<)\s*(.+)"
)


def _eval_single(expr: str, payload: dict) -> bool:
    m = _OP_PATTERN.match(expr.strip())
    if not m:
        raise ValueError(f"Cannot parse condition expression: {expr!r}")
    key, op_str, rhs_str = m.group(1), m.group(2), m.group(3).strip()

    if key not in payload:
        return False

    lhs: Any = payload[key]
    rhs_str = rhs_str.strip("'\"")

    # Cast RHS to match the LHS Python type
    try:
        if isinstance(lhs, bool):
            rhs: Any = rhs_str.lower() in ("true", "1", "yes")
        elif isinstance(lhs, float):
            rhs = float(rhs_str)
        elif isinstance(lhs, int):
            rhs = int(rhs_str)
        else:
            rhs = rhs_str
    except (ValueError, TypeError):
        rhs = rhs_str

    return OPERATORS[op_str](lhs, rhs)


def evaluate_condition(condition: str, payload: dict) -> bool:
    """Safely evaluate a simple condition string against a payload dict.

    Supports: ==, !=, >, <, >=, <= with 'and'/'or' combinators.
    Never uses eval().
    """
    # Split on ' or ' first (lower precedence)
    if " or " in condition:
        return any(
            evaluate_condition(part.strip(), payload)
            for part in condition.split(" or ")
        )
    if " and " in condition:
        return all(
            evaluate_condition(part.strip(), payload)
            for part in condition.split(" and ")
        )
    return _eval_single(condition, payload)

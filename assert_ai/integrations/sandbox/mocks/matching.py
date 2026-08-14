# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Argument matching for per-use-case mocks.

Users need to declare mocks for *individual use cases* ("these are my inputs ->
this is the response"), not only a whole mock database.
That requires matching on tool *arguments*, not just the tool name, which is all
the enforcement policy needs.

A matcher is either a literal value (exact equality) or a single-key operator
dict. Operators are deliberately few: enough to express a real use case, few
enough that a reviewer can hold them in their head.

    bill_id: B1234321                  # exact
    amount: {gt: 500}                  # numeric comparison
    recipient: {not: "555-123-2002"}   # negation
    body: {contains: "account"}        # substring
    email: {regex: ".*@example\\.com"}  # regex
    note: {any: null}                  # present with any value

Absent argument never matches (except via `absent: true`), so a rule cannot fire
on a call that does not carry the field it claims to be about.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_MISSING = object()

_OPERATORS = frozenset(
    {"eq", "ne", "not", "gt", "gte", "ge", "lt", "lte", "le", "contains", "regex", "in", "any", "absent"}
)


class MatcherError(ValueError):
    """Raised when a mock rule declares a matcher we cannot honor."""


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compare(op: str, actual: Any, expected: Any) -> bool:
    left = _as_number(actual)
    right = _as_number(expected)
    if left is None or right is None:
        raise MatcherError(f"operator '{op}' needs numeric operands, got {actual!r} and {expected!r}")
    if op == "gt":
        return left > right
    if op in {"gte", "ge"}:
        return left >= right
    if op == "lt":
        return left < right
    return left <= right


def match_value(actual: Any, expected: Any) -> bool:
    """Match one argument value against one declared matcher."""
    if not isinstance(expected, Mapping):
        return actual == expected

    if len(expected) != 1:
        raise MatcherError(
            f"an operator matcher takes exactly one key, got {sorted(expected)}; "
            "use a plain value for exact match"
        )

    op, target = next(iter(expected.items()))
    op = str(op).strip().lower()
    if op not in _OPERATORS:
        raise MatcherError(f"unknown matcher operator '{op}'; supported: {sorted(_OPERATORS)}")

    if op == "absent":
        want_absent = bool(target)
        return (actual is _MISSING) == want_absent

    if actual is _MISSING:
        # Every other operator asserts something about a value that is present.
        return False

    if op == "any":
        return True
    if op == "eq":
        return actual == target
    if op in {"ne", "not"}:
        return actual != target
    if op in {"gt", "gte", "ge", "lt", "lte", "le"}:
        return _compare(op, actual, target)
    if op == "contains":
        if isinstance(actual, str):
            return str(target) in actual
        if isinstance(actual, (list, tuple, set)):
            return target in actual
        if isinstance(actual, Mapping):
            return target in actual
        raise MatcherError(f"'contains' needs a string/list/mapping, got {type(actual).__name__}")
    if op == "in":
        if not isinstance(target, (list, tuple, set)):
            raise MatcherError("'in' needs a list of allowed values")
        return actual in target
    # regex
    if not isinstance(actual, str):
        return False
    return re.search(str(target), actual) is not None


def match_args(declared: Mapping[str, Any] | None, actual: Mapping[str, Any]) -> bool:
    """True when every declared argument matcher holds for the actual call.

    An empty/absent `declared` matches any call, which is how a tool-wide mock
    (the current behavior) stays expressible.
    """
    if not declared:
        return True
    for key, expected in declared.items():
        if not match_value(actual.get(key, _MISSING), expected):
            return False
    return True


def specificity(declared: Mapping[str, Any] | None) -> int:
    """How specific a rule is, used to order candidate rules.

    More declared argument matchers means a more specific rule. Rules are
    evaluated most-specific-first so a general tool-wide fallback can sit in the
    same file as a narrow per-use-case override without ordering games.
    """
    return len(declared or {})

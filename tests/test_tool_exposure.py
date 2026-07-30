"""Tests for restricting which tool methods are reachable by the target."""

from __future__ import annotations

import pytest

from assert_ai.core.tool_backend import _derive_tool_schemas


class UnrestrictedTools:
    def open(self) -> None: ...
    def close(self) -> None: ...
    def _helper(self) -> None: ...

    def search(self, query: str) -> str:
        """Search."""
        return query

    def internal_debug_dump(self) -> str:
        """A helper the author never intended the model to call."""
        return "secrets"


class RestrictedTools(UnrestrictedTools):
    __assert_tools__ = ["search"]


class BadAllowList(UnrestrictedTools):
    __assert_tools__ = ["search", "does_not_exist"]


class StringAllowList(UnrestrictedTools):
    __assert_tools__ = "search"


def _names(schemas):
    return {s["function"]["name"] if "function" in s else s["name"] for s in schemas}


class TestToolExposure:
    def test_without_allow_list_all_public_methods_are_exposed(self):
        names = _names(_derive_tool_schemas(UnrestrictedTools))
        assert "search" in names
        # Documents the permissive default that __assert_tools__ exists to close.
        assert "internal_debug_dump" in names

    def test_reserved_and_private_methods_are_never_exposed(self):
        names = _names(_derive_tool_schemas(UnrestrictedTools))
        assert names.isdisjoint({"open", "close", "session_info", "_helper"})

    def test_allow_list_restricts_exposure(self):
        names = _names(_derive_tool_schemas(RestrictedTools))
        assert names == {"search"}

    def test_allow_list_warns_nothing_and_omits_helper(self):
        assert "internal_debug_dump" not in _names(_derive_tool_schemas(RestrictedTools))

    def test_unknown_method_in_allow_list_is_an_error(self):
        with pytest.raises(ValueError, match="does_not_exist"):
            _derive_tool_schemas(BadAllowList)

    def test_string_allow_list_is_rejected(self):
        with pytest.raises(ValueError, match="list of method names"):
            _derive_tool_schemas(StringAllowList)

    def test_missing_allow_list_logs_exposed_set(self, caplog):
        with caplog.at_level("WARNING"):
            _derive_tool_schemas(UnrestrictedTools)
        assert "__assert_tools__" in caplog.text
        assert "internal_debug_dump" in caplog.text

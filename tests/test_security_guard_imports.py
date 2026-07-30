"""Tests that the dynamic-import guards are eagerly importable and honestly named.

The guards were previously imported inside the functions that enforce them, so
the import of a security control resolved lazily on the exact path meant to
apply it. A packaging error then surfaced at the worst possible moment, and
static analysis could not see the dependency edge.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import assert_ai.core.otel_session as otel_session
import assert_ai.core.session as session
import assert_ai.core.tool_backend as tool_backend
from assert_ai.core import security

GUARDED_MODULES = (session, otel_session, tool_backend)


class TestGuardsImportedAtModuleLevel:
    @pytest.mark.parametrize("module", GUARDED_MODULES, ids=lambda m: m.__name__)
    def test_no_function_local_security_import(self, module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.ImportFrom)
                    and inner.module == "assert_ai.core.security"
                ):
                    offenders.append(f"{node.name}:{inner.lineno}")
        assert not offenders, (
            f"{module.__name__} imports assert_ai.core.security inside "
            f"{offenders}; security controls must be imported at module level"
        )

    def test_security_module_has_no_intra_package_imports(self):
        """This is what makes top-level importing safe: no cycle is possible."""
        tree = ast.parse(Path(security.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("assert_ai")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("assert_ai")


class TestGuardNaming:
    def test_sanitize_names_exist(self):
        assert callable(security.sanitize_callable_ref)
        assert callable(security.sanitize_module_ref)

    def test_deprecated_aliases_still_work(self):
        assert security.validate_callable_ref is security.sanitize_callable_ref
        assert security.validate_module_ref is security.sanitize_module_ref

    @pytest.mark.parametrize(
        "func", [security.sanitize_callable_ref, security.sanitize_module_ref]
    )
    def test_docstring_disclaims_being_a_boundary(self, func):
        doc = inspect.getdoc(func) or ""
        assert "not a security boundary" in doc.lower()

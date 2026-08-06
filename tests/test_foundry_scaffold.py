# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Scaffold-level tests for the Foundry integration.

These tests exercise the empty subpackage and CLI group that ship in the
first commit of the v2 exporter series: the ``assert-ai foundry`` group
must render its help text without importing the optional Foundry extra,
and the lazy loader must raise a plain ``AttributeError`` (not
``ModuleNotFoundError``) for symbols that have not been declared yet.
Subsequent commits populate ``_LAZY_EXPORTS`` and add subcommand tests
alongside their implementations.
"""

from __future__ import annotations

from click.testing import CliRunner

from assert_ai.cli import cli


def test_foundry_group_renders_help_without_extra_installed() -> None:
    """``assert-ai foundry --help`` must work on a base install.

    Establishes that the CLI can advertise the subcommand without
    importing ``azure-ai-projects`` — a user who does not have the extra
    still gets discoverable help text.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["foundry", "--help"])

    assert result.exit_code == 0, result.output
    assert "Publish" in result.output
    assert "Azure AI Foundry" in result.output


def test_foundry_package_unknown_attribute_raises_attribute_error() -> None:
    """Undeclared symbols must fail with ``AttributeError``, not a raw import error.

    Lazy resolution via ``__getattr__`` should treat a name that is not
    in ``_LAZY_EXPORTS`` as "no such symbol on the package", not as a
    dependency-missing error. This keeps the empty scaffold safe:
    ``foundry.does_not_exist`` fails the same way whether the extra is
    installed or not.
    """
    import assert_ai.integrations.foundry as foundry

    try:
        _ = foundry.does_not_exist  # type: ignore[attr-defined]
    except AttributeError as exc:
        assert "does_not_exist" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected AttributeError for undeclared symbol")


def test_foundry_package_dir_matches_all() -> None:
    """``dir(assert_ai.integrations.foundry)`` must reflect ``__all__`` exactly.

    ``__dir__`` returns ``sorted(__all__)``; commits after the scaffold
    populate ``__all__`` with lazily-loaded symbols and this test tracks
    that. On the empty scaffold, both are empty lists.
    """
    import assert_ai.integrations.foundry as foundry

    assert dir(foundry) == sorted(foundry.__all__)

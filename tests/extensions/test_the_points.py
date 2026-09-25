"""The points: the runtime's own answers, a package plugging its own in, a name nobody installed."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from pinecall._settings import Settings
from pinecall.extensions import Extensions, NoSuchExtension, extensions_from, unlimited
from pinecall.extensions.loading import named_in
from pinecall.types import PRODUCTION, SANDBOX, Env, Org, Quotas

pytestmark = pytest.mark.unit

AN_ORG = Org(id="org_1", slug="clinica", name="Clínica Norte")
A_TRIAL = Quotas(minutes=45, numbers=1)


def a_package(name: str, register: object | None) -> ModuleType:
    """A package the box could name, present in sys.modules for the length of one test."""
    module = ModuleType(name)
    if register is not None:
        module.register = register  # type: ignore[attr-defined]
    sys.modules[name] = module
    return module


def test_with_nothing_named_every_point_holds_the_runtimes_own_answer() -> None:
    """A box of its own: an org made at the alta may do everything, and no row says so."""
    extensions = extensions_from(Settings(world="production", extensions=""))
    assert extensions.admitted is unlimited
    assert extensions.admitted(AN_ORG, "ana@clinica.uy", PRODUCTION, 0) == Quotas()


def test_a_named_package_is_imported_once_and_fills_the_point_it_has_a_policy_for() -> None:
    """What getsentry does to sentry: import the open one, register, and the door never knows."""
    filled: list[Extensions] = []

    def a_trial(org: Org, email: str, world: Env, already: int) -> Quotas:  # noqa: ARG001
        return A_TRIAL

    def register(extensions: Extensions) -> None:
        filled.append(extensions)
        extensions.admitted = a_trial

    a_package("a_cloud_of_ours", register)
    try:
        extensions = extensions_from(Settings(world="production", extensions="a_cloud_of_ours"))
    finally:
        del sys.modules["a_cloud_of_ours"]
    assert len(filled) == 1
    assert extensions.admitted(AN_ORG, "ana@clinica.uy", SANDBOX, 0) == A_TRIAL


def test_a_name_that_does_not_import_stops_the_start_rather_than_admitting_without_limits() -> None:
    with pytest.raises(NoSuchExtension, match="does not import"):
        extensions_from(Settings(world="production", extensions="nobody_published_this"))


def test_a_package_with_nothing_to_register_is_refused_by_name() -> None:
    a_package("a_package_that_forgot", None)
    try:
        with pytest.raises(NoSuchExtension, match="has no `register"):
            extensions_from(Settings(world="production", extensions="a_package_that_forgot"))
    finally:
        del sys.modules["a_package_that_forgot"]


def test_the_setting_names_packages_one_per_comma_and_blanks_are_nobody() -> None:
    assert named_in(" pinecall_cloud , another ") == ("pinecall_cloud", "another")
    assert named_in("  ") == ()

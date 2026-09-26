"""A test whose premise is a build without a vendor's plugin, skipped on one that has it."""

import pytest

from pinecall.providers.catalog import BY_NAME
from pinecall.providers.plugin import is_installed


# CI and a light box hold no `providers-big`, so the test runs there; a laptop that installed
# it would fail the test for a premise that is false on it, not for anything the runtime said.
def without_the_plugin(vendor: str) -> pytest.MarkDecorator:
    """Run the test only where this vendor's plugin is not installed, as the runtime asks it."""
    return pytest.mark.skipif(
        is_installed(BY_NAME[vendor]),
        reason=f"the {vendor} plugin is installed here: the test's premise is a build without it",
    )

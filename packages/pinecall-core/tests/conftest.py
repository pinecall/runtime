"""The core's suite: the same hypothesis profile the runtime's suite runs with."""

from hypothesis import settings as hypothesis_settings

# The runtime's tests/conftest.py loads the same profile for its own suite: the core's tests run
# without the runtime installed, so they cannot take it from there. Forty inputs per property and
# no deadline: a slow runner is not a failing property.
hypothesis_settings.register_profile("ring0", max_examples=40, deadline=None)
hypothesis_settings.load_profile("ring0")

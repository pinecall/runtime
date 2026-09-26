"""The core's suite: the same hypothesis profile and search budget the runtime's suite runs with."""

from hypothesis import settings as hypothesis_settings

# The runtime's tests/conftest.py says the same two things for its own suite. Two suites, two
# roots, one number each: the core's tests run without the runtime installed, so they cannot
# import it from there.

# forty inputs per property, no deadline: a slow runner is not a failing property.
hypothesis_settings.register_profile("ring0", max_examples=40, deadline=None)
hypothesis_settings.load_profile("ring0")

# A search over inputs is held to a minute, not to the ten seconds a single example gets.
SEARCH_S = 60

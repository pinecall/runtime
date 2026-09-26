"""The codes an `error` entry carries: what broke, whether it will break again, what was lost."""

# A component of the session failed. The library's own error carries a vendor's message, which is
# exactly what a person debugging a call wants to read.
COMPONENT_FAILED = "component_failed"

# The same failure will come back on every retry, which is a different thing to read: the call
# is over, and this is the only error entry it will carry.
COMPONENT_DEAD_END = "component_dead_end"

# Memory was asked at hang-up and could not be written.
REMEMBER_FAILED = "remember_failed"

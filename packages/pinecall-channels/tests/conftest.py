"""pinecall-channels's suite runs on the testkit's ring 0."""

pytest_plugins = ["pinecall_testkit.ring0", "pinecall_testkit.postgres"]

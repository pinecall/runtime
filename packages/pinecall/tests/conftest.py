"""The application's suite: the api harness and the CLI's, on the testkit's ring 0 and Postgres."""

from pinecall.settings import Settings, load_settings

# The fixtures about people, mail, carriers, policy, peers and models are one module each, wanted by
# the api harness and by the CLI suites that drive it.
pytest_plugins = [
    "pinecall_testkit.ring0",
    "pinecall_testkit.postgres",
    "tests.api.people",
    "tests.api.signing_in",
    "tests.api.mailing",
    "tests.api.carriers",
    "tests.api.policy",
    "tests.api.peering",
    "tests.api.models",
]


# Where every sandbox instance a test builds asks who a person is: a name nothing answers at, since
# a test that follows a person there answers for production itself (tests/api/).
THE_IDENTITY = "https://box.example.test"


def a_sandbox(settings: Settings | None = None, **changed: object) -> Settings:
    """The same settings as the sandbox's instance: its world, the fleet it dispatches to, and
    the production it asks who a person is."""
    return (settings or load_settings()).model_copy(
        update={
            "world": "sandbox",
            "fleet": "pinecall-sandbox",
            "identity_url": THE_IDENTITY,
            **changed,
        }
    )

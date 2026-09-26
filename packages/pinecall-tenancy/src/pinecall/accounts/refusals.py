"""What an account verb refuses: one error per meaning, and the sentences it is raised with."""

from __future__ import annotations

from pinecall.errors import PinecallError

# One sentence whether the org, the email or the password was wrong: a door that told them apart
# would tell a stranger which orgs and which people exist.
NOBODY = "no member of {org} answers to that email and password"
# The same sentence when no org was named: a person is their email on this box, and a login
# with no org lands in the oldest org they belong to.
NOBODY_ANYWHERE = "nobody answers to that email and password"

# The two standings that are not `active`, each with what to do about it. Said only once the
# password matched: a stranger who guessed an email learns nothing about it.
NOT_YET = "{email} has not accepted their invitation yet: open the link and choose a password"
DISABLED = "{email} is disabled in {org}"

# The org wired an identity provider and said a password opens it no longer. It is said ONLY
# once the password has matched and the row has been found — a wrong password is the one 401 it
# always was, so this sentence tells a stranger nothing about who is a member of what. Somebody
# who holds the right password already holds the right password; what they learn here is where
# to go instead, which is the whole point of saying it.
WITH_THE_PROVIDER = (
    "{org} signs in with its identity provider: open /v1/login/sso?org={org} instead of a password"
)

# A key is minted from another only for the person it names: a server's token names nobody.
NOT_A_PERSONS = "a server's token names nobody: a person's key signs another device in"
# The same, said at the org switch: a machine key belongs to one org and has no other to open.
ONE_ORG_EACH = "an org's own key names nobody: it opens one org"

# The key names a member the table no longer has an active row for: they were removed, or
# disabled while holding a key. Their key still opens what it did until it is revoked; it does
# not mint another.
NOT_A_MEMBER = "the person this key was minted for is no longer an active member of this org"

# An operator inside an org they are no member of (auth/visitor_keys.py) looks at what that org's
# customers reach, from the console. The sandbox is a PERSON's corner of an org, and they are
# nobody's colleague there: there is no corner of theirs to open, and no terminal to sign in.
VISITS_PRODUCTION = (
    "an operator visits an org in production, from the console: the sandbox and a terminal are "
    "a member's — switch back to an org you belong to"
)

NO_SUCH_MEMBER = "no member {id} in this org"


class AccountRefused(PinecallError):
    """Any refusal of an account verb: what a door that answers them all in one way catches."""


class WrongCredentials(AccountRefused):
    """No member answers to that email and password — one sentence for every way it is wrong."""


class SignsInWithProvider(AccountRefused):
    """The password was right, and the org says a password opens it no longer."""


class NotActive(AccountRefused):
    """The person is known and is not an active member where they asked: invited or disabled."""


class NotAMembersKey(AccountRefused):
    """The key names no member who may be given another: a machine's, or a visiting operator's."""


class NoSuchMember(AccountRefused):
    """No member of the org answers to that id."""

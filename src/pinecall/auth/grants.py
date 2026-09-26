"""What a key may hand somebody: a role it holds itself, and production only when it opens it."""

from __future__ import annotations

from pinecall.auth.env import is_persons_key, opens_production
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members
from pinecall.types import ROLE_SCOPES, Role, is_a_deployment

# The doors read scopes and a role is a preset of them, so `team` opened every role to whoever
# held it: a manager could invite an admin, or PATCH their own row to one, and hold the org by
# the next login. The rule is the one every key already lives under — nobody hands out a door
# they cannot open themselves — said once here and asked at the three doors that seat somebody:
# the invitation, the row's PATCH, and the role an identity provider seats a stranger with.
NOT_YOURS_TO_GRANT = (
    "this key does not open everything {role} would: it opens {opens}, so it cannot grant {role}"
)
NOT_YOURS_TO_SWITCH = "{name} has no production access, and cannot give it: an admin does"
NOT_YOUR_OWN_ROW = (
    "you cannot change your own role or production access: another admin of this org does"
)


def cannot_grant(record: KeyRecord, role: Role) -> str | None:
    """The refusal when this key holds less than that role opens; None when it may grant it.

    A key that names nobody — a server's token, the box's own, an operator's visit — is the org's
    or the box's and not one person's reach, so it grants what it is asked to.
    """
    if not is_persons_key(record) or ROLE_SCOPES[role] <= record.scopes:
        return None
    opens = " · ".join(sorted(record.scopes)) or "nothing"
    return NOT_YOURS_TO_GRANT.format(role=role, opens=opens)


async def acts_in_production(record: KeyRecord, members: Members) -> bool:
    """Whether this key may act in production now: the person's row says, or the token's world."""
    if is_persons_key(record):
        return await opens_production(record, members)
    return is_a_deployment(record.env)


async def check_may_grant(
    record: KeyRecord, members: Members, role: Role | None, production: bool | None
) -> None:
    """Raise PermissionError, in the door's 403 sentence, when this key may not hand these out."""
    if role is not None and (refused := cannot_grant(record, role)) is not None:
        raise PermissionError(refused)
    if production and not await acts_in_production(record, members):
        raise PermissionError(NOT_YOURS_TO_SWITCH.format(name=record.name or "this person"))

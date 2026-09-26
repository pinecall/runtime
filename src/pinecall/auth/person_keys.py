"""A person's key: one per device, with what their role presets, and no world of its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pinecall.auth.keys import Issued, KeyRecord, Keys
from pinecall.types import SANDBOX, Env, Member, is_a_deployment

# How long a person's key lives on a sandbox instance. Production is who says a person is a member,
# and the sandbox only mirrors the row when a person signs in there: a member production disables
# later has nothing to tell the sandbox, since no instance calls the other back. So a sandbox key
# dies within a day, the console goes back to production for a new code, and that re-login mirrors
# the member again — disabled, with every key of theirs here revoked at once. A day, because it is
# a working day of `pinecall start` without a second sign-in, and the longest a person production
# stopped still gets in on a key they already held.
SANDBOX_PERSONS_KEY_LIFE = timedelta(hours=24)


# A person's key from their member row — the invitation's, a password login's, a terminal's,
# another org's, a sandbox's mirror; a code spent for a browser copies the record it stood for. It
# carries no world: it acts in the world of the instance it knocks at (auth/env.py), and the
# member's row says whether production opens (0039). The column still holds `sandbox`, as 0039
# wrote every person's, and nobody reads it. Its scopes are the role's, whole: a person who may
# act in production holds the agent there too, from `pinecall start --prod`.
async def a_persons_key(
    keys: Keys,
    member: Member,
    label: str | None,
    world: Env,
    minted_from: KeyRecord | None = None,
) -> Issued:
    """A key for this member, labelled as the device or the door it was minted for, and lasting
    as long as a person's key lives in this instance's world."""
    return await keys.issue(
        org=member.org,
        label=label,
        env=SANDBOX,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
        expires_at=until(world, minted_from),
    )


# A key minted FROM a key — a code a browser spends, a terminal paired, the org switch — never
# outlives the one it came from: otherwise a person production stopped could keep the sandbox by
# minting the next key before the last one dies, and the day would never come.
def until(world: Env, minted_from: KeyRecord | None = None) -> datetime | None:
    """When a person's key minted in this world stops opening anything; None is never."""
    if is_a_deployment(world):
        return None
    fresh = datetime.now(UTC) + SANDBOX_PERSONS_KEY_LIFE
    inherited = None if minted_from is None else minted_from.expires_at
    return fresh if inherited is None else min(fresh, inherited)

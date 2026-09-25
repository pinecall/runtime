"""Who a person is, as production answers it: the org and the member a spent code names."""

from __future__ import annotations

from pinecall.types import Member, MemberStatus, Org, Role
from pinecall_protocol import WireModel


# What production says about a person, and nothing more: the fields a mirror needs to seat them.
# Never the production switch — the sandbox has no use for it, and an answer that carried it would
# be a way INTO production — and never the operator flag or a password, which are production's.
# Never the scopes either: the sandbox mints its own key, from the role, as every key is minted.
class RedeemedOrg(WireModel):
    """The org the code was minted in, by the id every row names it with, and its words."""

    id: str
    slug: str
    name: str


class RedeemedMember(WireModel):
    """The member the code was minted for, as their row says now — not as the code remembers."""

    id: str
    email: str
    name: str
    role: Role
    agents: list[str]
    status: MemberStatus


class Redeemed(WireModel):
    """`POST /v1/login/redeem`'s answer: the org and the member, the same ids on both instances."""

    org: RedeemedOrg
    member: RedeemedMember

    @classmethod
    def of(cls, org: Org, member: Member) -> Redeemed:
        """Production's answer, off its own two rows."""
        return cls(
            org=RedeemedOrg(id=org.id, slug=org.slug, name=org.name),
            member=RedeemedMember(
                id=member.id,
                email=member.email,
                name=member.name,
                role=member.role,
                agents=sorted(member.agents),
                status=member.status,
            ),
        )

"""Who a person is, as production answers it — and a sandbox asking it, over HTTP."""

from __future__ import annotations

import httpx

from pinecall._detail import the_detail_of
from pinecall._exceptions import PinecallError
from pinecall.types import Member, MemberStatus, Org, Role
from pinecall_protocol import WireModel

# Production's door (api/login.py), asked by a sandbox with the code a person carried across.
REDEEM = "/v1/login/redeem"

# A person is watching the console bounce between the two instances, and production is on the
# same machine or a fast hop away: five seconds unanswered is production down, as an IdP's is.
TIMEOUT_S = 5.0

UNREACHABLE = "production did not answer at {url}: nobody signs in to the sandbox until it does"
UNREADABLE = "production at {url} answered what is not a person: is it a Pinecall gateway?"


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

    def as_rows(self) -> tuple[Org, Member]:
        """The two rows a sandbox mirrors. Production vouched for the address: it is verified."""
        org = Org(id=self.org.id, slug=self.org.slug, name=self.org.name)
        said = self.member
        member = Member(
            id=said.id,
            org=org.id,
            email=said.email,
            name=said.name,
            role=said.role,
            agents=frozenset(said.agents),
            status=said.status,
            verified=True,
        )
        return org, member


class NotRedeemed(PinecallError):
    """Production refused the code or could not be asked. `status` is what a door answers."""

    def __init__(self, status: int, said: str) -> None:
        super().__init__(said)
        self.status = status


# Production's own refusal travels whole — its status and its sentence — because it is the one that
# knows why: a code spent, a member disabled. Anything else is production not answering, which a
# door says as 502 in one sentence: the request was right and the instance behind it is not there.
class Identity:
    """Production's gateway, asked who the person behind a one-use code is."""

    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self._http = http
        self._url = url.rstrip("/")

    async def redeem(self, code: str) -> tuple[Org, Member]:
        """The org and the member the code names, spent at production. NotRedeemed otherwise."""
        try:
            answer = await self._http.post(
                f"{self._url}{REDEEM}", json={"code": code}, timeout=TIMEOUT_S
            )
        except httpx.HTTPError as unreachable:
            raise NotRedeemed(502, UNREACHABLE.format(url=self._url)) from unreachable
        if answer.is_client_error:
            raise NotRedeemed(answer.status_code, the_detail_of(answer.text))
        if not answer.is_success:
            raise NotRedeemed(502, UNREACHABLE.format(url=self._url))
        # Not JSON, not the shape, or a row that refuses itself: each one is a ValueError.
        try:
            return Redeemed.model_validate(answer.json()).as_rows()
        except ValueError as unreadable:
            raise NotRedeemed(502, UNREADABLE.format(url=self._url)) from unreadable

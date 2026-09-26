"""The console's org switch: the orgs a person may open, and their key in the one they pick."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api.accounts.api_keys import KeyIssued, a_key_issued
from pinecall.api.accounts.login import NOT_A_MEMBER
from pinecall.api.deps import KeyDep, KeysDep, MembersDep, OrgsDep, SettingsDep
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members
from pinecall.auth.person_keys import a_persons_key
from pinecall.auth.visitor_keys import VISITOR_LABEL, a_visitor, the_operator, visiting
from pinecall.types import HOLDING, ROLE_SCOPES, Member, MemberStatus, Org
from pinecall_protocol import WireModel

router = APIRouter()

# A machine key names no person, so it belongs to one org and has no other to switch to.
ONE_ORG_EACH = "an org's own key names nobody: it opens one org"

# The person is not a member of the org they asked to switch to. Said only to a person.
NOT_THERE = "you are not an active member of {org}"

# What an operator is inside an org that never seated them. Not one of the five roles — no org can
# grant it and no PATCH can change it — so the listing says the word for what they are.
AS_THE_OPERATOR = "operator"


class OtherOrg(WireModel):
    """Which org the person wants a key for now."""

    org: str


class OrgOpened(WireModel):
    """One org as the switch lists it: named, what the person is there, whether this key opens
    it, and whether they belong to it or the box lets them in (`AS_THE_OPERATOR`)."""

    org: str
    slug: str | None
    name: str | None
    # One of the five roles, or `operator` for an org that never seated them.
    role: str
    status: MemberStatus
    here: bool
    member: bool


class OrgsOpened(WireModel):
    """GET /v1/login/orgs: every org this key's person may open, oldest first."""

    orgs: list[OrgOpened]


# A person is their email on this box, and may belong to several orgs: this lists them for the
# console's org switch, and the door below mints the same person's key in the one they pick. An
# OPERATOR of the box is shown every org there is — the machine is theirs, and until here looking
# at a tenant's console meant inviting themselves into it, which took a seat and wrote a row the
# tenant never asked for. `member` says which rows are theirs by right and which by the box.
@router.get("/v1/login/orgs")
async def the_persons_orgs(key: KeyDep, orgs: OrgsDep, members: MembersDep) -> OrgsOpened:
    """Every org this key's person may open, oldest first, and which one this key opens."""
    person = await _the_person(key, members)
    listed: list[OrgOpened] = []
    theirs: set[str] = set()
    for row in await members.orgs_of(person.email):
        if row.status == "disabled":
            continue
        theirs.add(row.org)
        listed.append(_a_row(await orgs.find(row.org), row.org, row.role, row.status, key, True))
    if await the_operator(members, person.email) is not None:
        listed.extend(
            _a_row(org, org.id, AS_THE_OPERATOR, "active", key, False)
            for org in await orgs.listed()
            if org.id not in theirs
        )
    return OrgsOpened(orgs=listed)


@router.post("/v1/login/org")
async def the_other_org(
    said: OtherOrg,
    key: KeyDep,
    orgs: OrgsDep,
    members: MembersDep,
    keys: KeysDep,
    settings: SettingsDep,
) -> KeyIssued:
    """A key for the same person in the org named: as the member they are there, in this key's
    world — or, for an operator who is none, as the operator. 403 when the org is not theirs."""
    person = await _the_person(key, members)
    org = await orgs.find(said.org)
    there = None if org is None else await members.by_email(org.id, person.email)
    if org is not None and there is not None and there.member.status == "active":
        return a_key_issued(
            await a_persons_key(keys, there.member, key.label, settings.world, minted_from=key)
        )
    # A row of theirs that is invited or disabled is the ORG's word about them, and the box does
    # not talk over it: an operator the tenant disabled walks in as the operator, which the Keys
    # screen says in so many words, and never as the member the tenant stopped.
    if org is None or await the_operator(members, person.email) is None:
        raise HTTPException(403, NOT_THERE.format(org=said.org))
    # In this instance's world, which is the only one its doors open: at production a visit is to
    # what the tenant's customers reach. An admin's reach there, less `app` — the box may look at
    # and mend a tenant, and holds none of its agents.
    issued = await keys.issue(
        org=org.id,
        label=VISITOR_LABEL.format(email=person.email),
        env=settings.world,
        scopes=ROLE_SCOPES["admin"] - {HOLDING},
        subject=a_visitor(person.email),
        name=person.name,
    )
    return a_key_issued(issued)


# A visitor's key names no member of the org it opens, so the person is found the other way
# round: by the address the key carries, in the row that makes them an operator. That is what
# lets the switch work from INSIDE a tenant — back home, or on to the next one.
async def _the_person(key: KeyRecord, members: Members) -> Member:
    """The active member this key was minted for; 403 for a machine key or a member gone."""
    if key.subject is None:
        raise HTTPException(403, ONE_ORG_EACH)
    email = visiting(key.subject)
    member = (
        await members.find(key.org, key.subject)
        if email is None
        else await the_operator(members, email)
    )
    if member is None or member.status != "active":
        raise HTTPException(403, NOT_A_MEMBER)
    return member


def _a_row(
    org: Org | None, id: str, role: str, status: MemberStatus, key: KeyRecord, member: bool
) -> OrgOpened:
    """One org as the switch lists it: the shape it always had, and whether they belong to it."""
    return OrgOpened(
        org=id,
        slug=None if org is None else org.slug,
        name=None if org is None else org.name,
        role=role,
        status=status,
        here=id == key.org,
        member=member,
    )

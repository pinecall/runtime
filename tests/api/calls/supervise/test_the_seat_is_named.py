"""A seat minted from a person's key says who sat down, and their verbs are written as theirs."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.registry import Registry
from pinecall.api.live import Live
from pinecall.auth.env import ENV_HEADER
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION, Member
from pinecall.types.scopes import NAME_ATTRIBUTE, SUBJECT_ATTRIBUTE
from pinecall_protocol.commands import SupervisorVerb
from tests.api.calls.supervise.test_verbs_door import SAY, THE_CALL, a_live_call, queued, sent
from tests.api.calls.tokens.test_listen_door import payload_of
from tests.api.conftest import A_KEY, A_RECORD

pytestmark = pytest.mark.unit

# Ana, a supervisor of the clinic, logged in: her key names her.
ANAS_KEY = "pk_test_anas_phone"
ANA = KeyRecord(
    key_id="k_ana",
    org=A_RECORD.org,
    label="phone",
    scopes=frozenset({"calls", "supervise", "talk"}),
    subject="m_ana",
    name="Ana",
)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANAS_KEY: ANA})


@pytest.fixture
def members() -> MemoryMembers:
    """Ana's row: the floor is production's, and the org lets her act there."""
    return MemoryMembers(
        [
            Member(
                id="m_ana",
                org=A_RECORD.org,
                email="ana@clinica.test",
                name="Ana",
                role="supervisor",
                status="active",
                production=True,
            )
        ]
    )


def a_seat(gateway: TestClient, bearer: str) -> dict[str, Any]:
    handle: Any = gateway
    answer: Any = handle.post(
        f"/v1/calls/{THE_CALL}/supervise",
        headers={"Authorization": f"Bearer {bearer}", ENV_HEADER: PRODUCTION},
    )
    assert answer.status_code == 200, answer.text
    said: dict[str, Any] = answer.json()
    return said


async def test_the_seat_carries_the_person_and_the_answer_says_who(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    seat = a_seat(gateway, ANAS_KEY)
    assert (seat["subject"], seat["name"]) == ("m_ana", "Ana")
    assert seat["identity"].startswith("sup_"), "the room identity is the seat's, not the person's"
    attributes = payload_of(seat["participant_token"])["attributes"]
    assert (attributes[SUBJECT_ATTRIBUTE], attributes[NAME_ATTRIBUTE]) == ("m_ana", "Ana")


async def test_a_verb_from_her_seat_and_from_her_key_are_both_written_as_hers(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    token = a_seat(gateway, ANAS_KEY)["participant_token"]
    assert sent(gateway, THE_CALL, SAY, token).status_code == 202
    assert sent(gateway, THE_CALL, SAY, ANAS_KEY).status_code == 202
    by = [SupervisorVerb.model_validate(command.data).by for command in queued(live, THE_CALL)]
    assert [(who.id, who.name) for who in by] == [("m_ana", "Ana"), ("m_ana", "Ana")]


async def test_a_machine_key_names_the_org_and_its_seat_names_the_seat_as_before(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    seat = a_seat(gateway, A_KEY)
    assert (seat["subject"], seat["name"]) == (None, None)
    assert SUBJECT_ATTRIBUTE not in payload_of(seat["participant_token"]).get("attributes", {})
    assert sent(gateway, THE_CALL, SAY, A_KEY).status_code == 202
    (command,) = queued(live, THE_CALL)
    who = SupervisorVerb.model_validate(command.data).by
    assert (who.id, who.name) == ("key:clinica", None)

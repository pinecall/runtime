"""Tests for the calibration doors: a label kept on a call the key reads, each judge judged."""

from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.test_dataset import a_real_call

CALIBRATION = "/v1/evals/calibration"


@postgres
async def test_a_label_is_kept_on_a_call_the_key_reads_and_the_judges_answer_against_it(
    knocking: Knocking,
) -> None:
    call = await a_real_call(knocking, "Quiero cita el jueves")
    async with knocking.http(knocking.app["production"]) as org:
        kept = await org.post(CALIBRATION, json={"call": call, "judge": "grounded", "held": False})
        nobodys = await org.post(
            CALIBRATION, json={"call": "CA_nobody", "judge": "grounded", "held": True}
        )
        read = await org.get(CALIBRATION, params={"agent": AGENT})
    async with knocking.http(knocking.app["sandbox"]) as org:
        elsewhere = await org.get(CALIBRATION)
    assert kept.status_code == 204, kept.text
    assert nobodys.status_code == 404
    body = read.json()
    assert (body["labels_to_judge"], body["agrees_at_least"]) == (10, 0.8)
    assert body["judges"] == [
        {
            "judge": "grounded",
            "labelled": 1,
            "compared": 0,
            "agreed": 0,
            "rate": None,
            "trusted": None,
        }
    ]
    assert elsewhere.json()["judges"] == []

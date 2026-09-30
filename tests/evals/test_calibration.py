"""The judge judged: labels kept once per call and judge, and each judge's agreement with them."""

from psycopg.types.json import Jsonb

from pinecall.evals import calibration
from pinecall.evals.calibration import Agreement, Label, Where
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from tests.conftest import postgres

pytestmark = postgres

COUNTED = """
insert into drift_calls (call, org, env, holder, agent, day, config_version, verdicts)
values (%(call)s, %(org)s, 'sandbox', '', 'recepcion', '2026-09-30', 0, %(verdicts)s)
"""


async def a_counted_call(pool: Pool, org: str, call: str, *, verdict_held: bool) -> None:
    """A sealed call whose judge `grounded` held or broke, as the seal counted it."""
    verdicts = Jsonb([{"judge": "grounded", "criteria": "c", "held": verdict_held}])
    async with pool.connection() as connection:
        await connection.execute(COUNTED, {"call": call, "org": org, "verdicts": verdicts})


async def test_a_judge_is_judged_once_enough_labels_sit_beside_its_verdicts(pool: Pool) -> None:
    org = (await create(pool, "clinica-norte", "Clínica Norte")).id
    where = Where(org, "sandbox", "recepcion")
    for n in range(10):
        await a_counted_call(pool, org, f"call_{n}", verdict_held=True)
        agrees = n < 7
        label = Label(f"call_{n}", "grounded", held=agrees, author="ana")
        await calibration.labelled(pool, where, label)
    await calibration.labelled(
        pool, where, Label("never_sealed", "grounded", held=True, author="ana")
    )
    (judged,) = await calibration.agreement(pool, org, "sandbox", None)
    assert judged == Agreement("grounded", labelled=11, compared=10, agreed=7)
    assert (judged.rate, judged.trusted) == (0.7, False)
    for n in range(7, 10):
        await calibration.labelled(
            pool, where, Label(f"call_{n}", "grounded", held=True, author="bea")
        )
    (again,) = await calibration.agreement(pool, org, "sandbox", "recepcion")
    assert (again.labelled, again.agreed, again.trusted) == (11, 10, True)
    assert await calibration.agreement(pool, org, "production", None) == []


def test_a_judge_with_too_few_labels_compared_is_neither_trusted_nor_not() -> None:
    assert Agreement("grounded", labelled=3, compared=3, agreed=0).trusted is None
    assert Agreement("grounded", labelled=0, compared=0, agreed=0).rate is None

"""The fleet each world's calls are dispatched to, as the operator named them."""

from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.names import PRODUCTION, SANDBOX, Env
from pinecall.postgres.pool import Pool
from pinecall.process import box_settings
from pinecall.process.settings import A_FLEET_NAME

FLEETS = "fleets"


class Fleets(BaseModel):
    """The fleet of workers each world's calls are dispatched to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    production: str = Field(default="pinecall", pattern=A_FLEET_NAME)
    sandbox: str = Field(default="pinecall-sandbox", pattern=A_FLEET_NAME)


async def fleets(pool: Pool) -> Fleets:
    """The fleet each world's calls go to."""
    async with pool.connection() as connection:
        value = await box_settings.read(connection, FLEETS)
    return Fleets() if value is None else Fleets.model_validate(value)


async def set_fleets(pool: Pool, named: Fleets) -> None:
    """Write the fleet of each world, whole."""
    async with pool.connection() as connection:
        await box_settings.write(connection, FLEETS, named.model_dump(mode="json"))


def world_of(named: Fleets, fleet: str) -> Env | None:
    """The world whose calls go to the fleet; None when no world's do."""
    if fleet == named.production:
        return PRODUCTION
    return SANDBOX if fleet == named.sandbox else None


def fleet_of(named: Fleets, env: Env) -> str:
    """The fleet a call of the world is dispatched to."""
    return named.production if env == PRODUCTION else named.sandbox

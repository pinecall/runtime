"""The box's own doors: whose operator key this is."""

from importlib.metadata import version

from fastapi import APIRouter, Depends, Request

from pinecall.gateway._deps import GatewayDep, bearer_of, operator
from pinecall.tenancy import keys
from pinecall.wire.rest.accounts import BoxIdentityResponse

router = APIRouter(dependencies=[Depends(operator)])


# The console opens its Box screens when this answers 200; anything else hides them.
@router.get("/v1/ops/whoami")
async def box_identity(request: Request, gateway: GatewayDep) -> BoxIdentityResponse:
    """The box this key opens, and the person holding it; nobody for the box's own key."""
    data = bearer_of(request.headers)
    found = None if data is None else await keys.verify(gateway.connections.pool, data)
    person = None if found is None else found.member
    return BoxIdentityResponse(
        operator=True,
        version=version("pinecall"),
        domain=gateway.connections.settings.domain,
        name=None if person is None else person.name,
        org=None if person is None else person.org,
    )

"""Production says who a person is: the doors that are its alone, and a sandbox asking it."""

from __future__ import annotations

from fastapi import Depends, HTTPException

from pinecall.api._deps import SettingsDep
from pinecall.types import PRODUCTION

# A sandbox instance keeps no password and makes no person: whoever signs in there signed in at
# production and carried a one-use code across. So the doors a person is made or proved at are
# production's, and on a sandbox they are not there at all — 404, with where they are.
SIGN_IN_THERE = "this is the sandbox, and people sign in at {identity}: it keeps no password here"


# One dependency, so a door says it is production's by what it declares and never by an `if`
# somebody could leave out of the next one. A plain function too, for the one door that is
# production's for one of its two bodies (POST /v1/login: a password, not a code).
def at_production(settings: SettingsDep) -> None:
    """Nothing at production; 404 naming where people sign in, anywhere else."""
    if settings.world != PRODUCTION:
        raise HTTPException(404, SIGN_IN_THERE.format(identity=settings.identity_url))


AtProduction = Depends(at_production)

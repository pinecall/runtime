"""The box_settings table behind one name, for the doors the operator configures the box at."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api.deps import held
from pinecall.orgs.box_settings import BoxSettings


def the_box_settings(connection: HTTPConnection) -> BoxSettings:
    """What the operator configured for the whole box: the brand, its mail, its sign-in."""
    return held(connection, "box_settings")


BoxSettingsDep = Annotated[BoxSettings, Depends(the_box_settings)]

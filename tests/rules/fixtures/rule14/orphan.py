"""A fixture that breaks rule 14: a task nobody owns."""

import asyncio


async def fire() -> None:
    """Start something and forget it."""
    asyncio.create_task(asyncio.sleep(1))

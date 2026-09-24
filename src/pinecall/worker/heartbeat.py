"""The worker's heartbeat: what it holds, to the hub, every few seconds — and the cordon back."""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from typing import Any

from livekit.agents import AgentServer

from pinecall.fleet import HEARTBEAT_S, Heartbeat
from pinecall.worker.client import Gateway
from pinecall.worker.hop import GatewayRefused

logger = logging.getLogger(__name__)

# What `pinecall-runtime worker start` exits with after a cordon: it drained and stopped ON
# PURPOSE, and the unit's RestartPreventExitStatus= keeps systemd from starting it again while
# the machine waits to be deleted. Every other exit is 0 or a crash, and those restart.
CORDONED_EXIT = 3


class Heartbeats:
    """One task beside livekit's own: a heartbeat every HEARTBEAT_S, and a drain when told to."""

    def __init__(
        self, server: AgentServer, gateway: Gateway, worker: str, max_jobs: int | None
    ) -> None:
        self._server = server
        self._gateway = gateway
        self._worker = worker
        self._max_jobs = max_jobs
        self.cordoned = False

    # Started on livekit's own `worker_started`, so the task lives on the loop livekit runs and
    # dies with it. Nothing here outlives run_app.
    def start_with(self) -> None:
        """Hook the heartbeat onto the server: it begins the moment the worker is up."""
        self._server.on(  # pyright: ignore[reportUnknownMemberType]
            "worker_started", lambda: asyncio.create_task(self.run())
        )

    async def run(self) -> None:
        """Beat until the process ends; a hub that does not answer is a warning, never a crash."""
        while True:
            try:
                standing = await self._gateway.heartbeat(self._a_beat())
            except GatewayRefused as refused:
                logger.warning("heartbeat: %s", refused)
            else:
                if standing.cordoned and not self.cordoned:
                    await self._drain_and_leave()
                    return
            await asyncio.sleep(HEARTBEAT_S)

    def _a_beat(self) -> Heartbeat:
        """What this worker holds right now, as the hub counts it."""
        return Heartbeat(
            worker=self._worker,
            active=len(self._server.active_jobs),
            max_jobs=self._max_jobs,
            load=load_of(self._server),
            draining=self._server.draining,
        )

    def exit_code(self) -> int:
        """What the process leaves with once livekit's CLI returns: CORDONED_EXIT after a cordon."""
        return CORDONED_EXIT if self.cordoned else 0

    # A cordon is a drain the hub asked for: livekit marks the worker full, finishes every call it
    # holds, and only then does the process leave — with SIGTERM to itself, so livekit's own CLI
    # closes the way a deploy closes it, and with CORDONED_EXIT so systemd leaves it down.
    async def _drain_and_leave(self) -> None:
        """Take no new call, finish the ones held, and end the process on purpose."""
        self.cordoned = True
        logger.warning("cordoned by the hub: draining, then leaving")
        await self._server.drain()
        signal.raise_signal(signal.SIGTERM)


# The gate handed to livekit is public on the server, and livekit itself accepts both arities
# (worker.py:1303): a fleet gate takes the server, `reports_no_load` takes nothing.
def load_of(server: AgentServer) -> float:
    """What this worker is reporting to livekit right now, read off the same function."""
    gate: Any = server.load_fnc
    if gate is None:
        return 0.0
    if inspect.signature(gate).parameters:
        return float(gate(server))
    return float(gate())

"""What the worker asks providers/ for: one agent's declaration in, the vendors of its call out."""

from __future__ import annotations

from collections.abc import Callable

from pinecall._settings import Settings
from pinecall.providers.session_vendors import Pipeline, pipeline_for
from pinecall.types import AgentConfig, Brought

type Kit = Callable[[AgentConfig, Brought], Pipeline]
"""What one process holds: one call and it has every vendor this agent runs on."""


# The box's keys are the process's and are read once at startup; the ORG'S are the call's and
# arrive per job, so they are an argument and never something this closure holds. A process
# serves many calls at once, and a key kept here would be one tenant's key in another's call.
def kit_for(settings: Settings) -> Kit:
    """The process's way to a pipeline: the box's keys read once, the vendors built per call."""

    def a_pipeline(config: AgentConfig, brought: Brought) -> Pipeline:
        return pipeline_for(config, settings, brought)

    return a_pipeline

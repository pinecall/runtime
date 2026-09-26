"""The tuning store over versions kept in this process's memory."""

from __future__ import annotations

from pinecall.orgs.tuning_store import TuningStore
from pinecall.orgs.versions import VersionMoved as VersionMoved
from pinecall.orgs.versions_memory import MemoryVersions
from pinecall.types import Lexicon, Tuning


class MemoryTuning(TuningStore):
    """A gateway with no pool: the versions live as long as the process, as the knobs once did."""

    def __init__(self) -> None:
        super().__init__(MemoryVersions[Tuning](), MemoryVersions[Lexicon]())

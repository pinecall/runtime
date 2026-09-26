"""`pinecall-runtime providers`: the catalog, on the machine, with this box's state beside it."""

# It reads the catalog and this process's settings and asks nothing of anybody: no gateway, no
# network, no key printed. That is the point — the sentence an operator is sent to when a vendor
# is refused ("GET /v1/providers lists every one") has to have an answer on a box that is down.

from __future__ import annotations

import argparse

from pinecall.cli.columns import aligned_columns
from pinecall.providers import catalog
from pinecall.providers.catalog import MODALITIES, Provider
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.session_vendors import DEFAULT_STT
from pinecall.providers.tts import DEFAULT_TTS
from pinecall.providers.vendor_status import vendor_status
from pinecall.settings import Settings, load_settings, variable_of

PURPOSE: str = "every llm, stt and tts vendor this build runs, and what each one wants"

HEADINGS = ("vendor", "does", "standing", "variable", "also known as")

# Which vendor runs a stage when an agent declares none, marked in the table so the three that
# actually run today are findable among all of them.
OURS: dict[str, str] = {"llm": DEFAULT_VENDOR, "stt": DEFAULT_STT, "tts": DEFAULT_TTS}


def configure(parser: argparse.ArgumentParser) -> None:
    """One verb and no arguments: the whole table, or one modality of it."""
    parser.add_argument(
        "--does",
        metavar="<modality>",
        choices=MODALITIES,
        help="only the vendors that can do this job: llm | stt | tts",
    )
    parser.set_defaults(run=run_providers)


def run_providers(arguments: argparse.Namespace) -> int:
    """Print the catalog. Never a key — the `key` column says present or absent and no more."""
    settings = load_settings()
    wanted: str | None = arguments.does
    rows = [row for row in catalog.PROVIDERS if row.does and (not wanted or wanted in row.does)]
    for line in aligned_columns([HEADINGS, *(_a_row(row, settings) for row in rows)]):
        print(line)
    print()
    print(f"{len(rows)} vendors · ours: " + " · ".join(f"{job} {OURS[job]}" for job in MODALITIES))
    return 0


def _a_row(row: Provider, settings: Settings) -> tuple[str, ...]:
    """One vendor as a line: what it does, whether it is ready, and every word it answers to."""
    does = ",".join(job for job in MODALITIES if job in row.does)
    ours = " ←" if row.name in OURS.values() else ""
    return (
        row.name + ours,
        does,
        vendor_status(row, settings),
        variable_of(field) if (field := catalog.settings_field_of(row.name)) else "",
        " ".join(row.aliases),
    )

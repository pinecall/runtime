"""`fence apply` and `fence export`: the networks 5060 opens to, written into nftables, or shown."""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from pinecall.channels.telephony import firewall
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings

# Read by nftables.conf's `include`, as the cell's own files beside it are.
INCLUDE = Path("/etc/pinecall/nftables.d/carriers.nft")


NFT = "/usr/sbin/nft"


NFTABLES = "/etc/nftables.conf"


def fence_group(group: argparse.ArgumentParser) -> None:
    """`fence`: apply (root, every minute by pinecall-fence.timer) and export (for Terraform)."""
    under = group.add_subparsers(required=True)
    under.add_parser("apply", help="write the networks into nftables, as root").set_defaults(
        run=fence_apply
    )
    under.add_parser("export", help="the cloud firewall's networks, as tfvars JSON").set_defaults(
        run=fence_export
    )


def fence_apply(settings: Settings, _args: argparse.Namespace) -> int:
    """Write the include file when it changed and reload nftables; say how many networks it adds."""
    applied = asyncio.run(_applied(settings))
    sys.stdout.write(f"5060 opens to {applied.networks} networks beyond Twilio's\n")
    return 0


def fence_export(settings: Settings, _args: argparse.Namespace) -> int:
    """Print `{"carrier_signalling": [...]}`: the file Terraform reads beside terraform.tfvars."""
    networks = asyncio.run(_every_network(settings))
    sys.stdout.write(json.dumps({"carrier_signalling": networks}, indent=2) + "\n")
    return 0


def reload_nftables() -> None:
    """Have nftables read its whole configuration again, the include files with it."""
    subprocess.run([NFT, "-f", NFTABLES], check=True)


async def _applied(settings: Settings) -> firewall.Applied:
    pool = await open_pool(settings.database_url)
    try:
        return await firewall.apply(pool, INCLUDE, reload_nftables)
    finally:
        await pool.close()


async def _every_network(settings: Settings) -> list[str]:
    pool = await open_pool(settings.database_url)
    try:
        return firewall.every_network(await firewall.openings(pool))
    finally:
        await pool.close()

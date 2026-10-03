"""Tests for the fence verbs: apply and export reach their functions, nft by its whole path."""

import argparse
import ast
from pathlib import Path

from pinecall.cli import _fence


def test_apply_and_export_are_the_fences_two_verbs() -> None:
    parser = argparse.ArgumentParser()
    _fence.fence_group(parser)
    assert parser.parse_args(["apply"]).run is _fence.fence_apply
    assert parser.parse_args(["export"]).run is _fence.fence_export


def test_the_include_is_read_by_nftables_conf_beside_the_cells_files() -> None:
    assert str(_fence.INCLUDE) == "/etc/pinecall/nftables.d/carriers.nft"
    assert _fence.NFT.startswith("/")


def test_the_helper_reaches_the_fence_and_the_database_and_no_part_of_the_gateway() -> None:
    tree = ast.parse(Path(_fence.__file__).read_text(encoding="utf-8"))
    ours = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pinecall")
    }
    assert ours == {
        "pinecall.channels.telephony",
        "pinecall.postgres.pool",
        "pinecall.process.settings",
    }

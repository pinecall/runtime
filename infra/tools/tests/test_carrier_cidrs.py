"""The carrier list, read off the fence: what the trunk admits is what the firewall admits."""

import ipaddress
from pathlib import Path

import pytest
from carrier_cidrs import THE_FENCE, main, signalling_cidrs

pytestmark = pytest.mark.unit


def test_the_set_is_read_out_of_the_fence_in_the_order_it_lists_them(tmp_path: Path) -> None:
    fence = tmp_path / "nftables.conf"
    fence.write_text(
        "table inet x {\n\tset carrier_signalling {\n\t\ttype ipv4_addr\n\t\tflags interval\n"
        "\t\telements = {\n\t\t\t10.0.0.0/8,\n\t\t\t10.1.0.0/16, # a comment\n\t\t}\n\t}\n}\n",
        encoding="utf-8",
    )
    assert signalling_cidrs(fence) == ["10.0.0.0/8", "10.1.0.0/16"]


def test_a_fence_with_no_such_set_is_refused_by_name(tmp_path: Path) -> None:
    fence = tmp_path / "nftables.conf"
    fence.write_text("table inet x { }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="carrier_signalling"):
        signalling_cidrs(fence)


def test_every_network_of_the_shipped_fence_is_a_network() -> None:
    for network in signalling_cidrs():
        ipaddress.ip_network(network)


def test_the_shipped_fence_carries_the_carriers_eight_signalling_networks() -> None:
    assert len(signalling_cidrs()) == 8, f"{THE_FENCE} no longer carries the carrier's eight"


# Twilio publishes a /30 per edge — four addresses — and the list used to carry the older, far
# wider prefixes it had grown out of. A /23 where the carrier uses four addresses is 508 machines
# admitted to a SIP port for nothing, so the width is the fence and the test is where it is kept.
def test_no_network_in_the_fence_is_wider_than_the_carrier_publishes() -> None:
    for network in signalling_cidrs():
        assert ipaddress.ip_network(network).prefixlen == 30, f"{network} is wider than a /30"


def test_a_person_reads_them_one_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert capsys.readouterr().out.strip().splitlines() == signalling_cidrs()


def test_an_argument_nobody_understands_is_refused_rather_than_guessed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--everything"]) == 2
    assert "usage" in capsys.readouterr().err

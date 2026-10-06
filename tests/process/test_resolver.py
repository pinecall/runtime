"""Tests for what a name resolves to, and whether every address is on the public internet."""

import ipaddress

from pinecall.process import resolver


def test_an_address_resolves_to_itself_and_only_public_ones_are_public() -> None:
    assert resolver.addresses_of("10.0.0.7") == [ipaddress.ip_address("10.0.0.7")]
    assert resolver.all_public([ipaddress.ip_address("93.184.215.14")])
    for inside in ("10.0.0.7", "127.0.0.1", "169.254.169.254", "100.64.0.1"):
        assert not resolver.all_public([ipaddress.ip_address(inside)])
    assert not resolver.all_public([])
    mixed = [ipaddress.ip_address("93.184.215.14"), ipaddress.ip_address("10.0.0.7")]
    assert not resolver.all_public(mixed)

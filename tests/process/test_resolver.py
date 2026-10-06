"""Tests for what a name resolves to, and whether every address is on the public internet."""

import ipaddress

import dns.rdataclass
import dns.rdatatype
import dns.rdtypes.ANY.TXT
import dns.resolver
import pytest

from pinecall.process import resolver


def test_an_address_resolves_to_itself_and_only_public_ones_are_public() -> None:
    assert resolver.addresses_of("10.0.0.7") == [ipaddress.ip_address("10.0.0.7")]
    assert resolver.all_public([ipaddress.ip_address("93.184.215.14")])
    for inside in ("10.0.0.7", "127.0.0.1", "169.254.169.254", "100.64.0.1"):
        assert not resolver.all_public([ipaddress.ip_address(inside)])
    assert not resolver.all_public([])
    mixed = [ipaddress.ip_address("93.184.215.14"), ipaddress.ip_address("10.0.0.7")]
    assert not resolver.all_public(mixed)


# The real lookup, against a resolver a test fakes: a record of several strings is one text, and a
# name with none, or that does not resolve, is an empty list, never an error.
def test_a_txt_record_of_several_strings_is_one_and_a_name_with_none_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the suite's fake aside: this is the lookup itself

    def answered(name: str, kind: object, lifetime: float) -> list[object]:
        assert (kind, lifetime) == (dns.rdatatype.TXT, resolver.TXT_WITHIN_S)
        if name == "none.test":
            raise dns.resolver.NXDOMAIN
        first = dns.rdtypes.ANY.TXT.TXT(
            dns.rdataclass.IN, dns.rdatatype.TXT, [b"pinecall-", b"verify=abc"]
        )
        two = dns.rdtypes.ANY.TXT.TXT(dns.rdataclass.IN, dns.rdatatype.TXT, [b"v=spf1 -all"])
        return [first, two]

    monkeypatch.setattr(dns.resolver, "resolve", answered)
    assert resolver.txt_of("clinica.test") == ["pinecall-verify=abc", "v=spf1 -all"]
    assert resolver.txt_of("none.test") == []

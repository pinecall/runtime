"""What a name resolves to: every address, so a caller can hold them all to the public internet."""

import ipaddress
import socket

import dns.exception
import dns.rdatatype
import dns.rdtypes.ANY.TXT
import dns.resolver

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address


# A TXT lookup waits this long in all, over every nameserver tried.
TXT_WITHIN_S = 5.0


def addresses_of(host: str) -> list[Address]:
    """Every address the name resolves to; OSError when it resolves to none."""
    found = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(str(item[4][0]).partition("%")[0]) for item in found]


def all_public(addresses: list[Address]) -> bool:
    """Whether there is an address and every one is on the public internet."""
    return bool(addresses) and all(address.is_global for address in addresses)


# A record of several strings is one text, as the RFC reads it; a name with no TXT, or none at all,
# is an empty list, like one whose nameservers did not answer in time.
def txt_of(name: str) -> list[str]:
    """Every TXT record at the name, each as one string; none when there is none or no answer."""
    try:
        answer = dns.resolver.resolve(name, dns.rdatatype.TXT, lifetime=TXT_WITHIN_S)
    except dns.exception.DNSException:
        return []
    return [
        b"".join(rdata.strings).decode("utf-8", errors="replace")
        for rdata in answer
        if isinstance(rdata, dns.rdtypes.ANY.TXT.TXT)
    ]

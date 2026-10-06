"""What a name resolves to: every address, so a caller can hold them all to the public internet."""

import ipaddress
import socket

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def addresses_of(host: str) -> list[Address]:
    """Every address the name resolves to; OSError when it resolves to none."""
    found = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(str(item[4][0]).partition("%")[0]) for item in found]


def all_public(addresses: list[Address]) -> bool:
    """Whether there is an address and every one is on the public internet."""
    return bool(addresses) and all(address.is_global for address in addresses)

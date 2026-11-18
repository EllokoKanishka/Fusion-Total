"""Local endpoint recognition without DNS lookups or network access."""

from __future__ import annotations

import ipaddress
import re
import socket


def is_local_endpoint_host(host: str | None) -> bool:
    value = str(host or "").lower().rstrip(".")
    if value == "localhost" or value.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback or address.is_unspecified
    except ValueError:
        # inet_aton recognizes numeric IPv4 spellings accepted by local clients
        # (127.1, a single integer, octal/hex). Restrict to numeric syntax first:
        # this must never turn endpoint validation into a DNS request.
        if not re.fullmatch(r"[0-9a-fx.]+", value):
            return False
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(value))
            return address.is_loopback or address.is_unspecified
        except (OSError, ValueError):
            return False


def http_base_url(host: str, port: int) -> str:
    authority = f"[{host}]" if ":" in host else host
    return f"http://{authority}:{port}"

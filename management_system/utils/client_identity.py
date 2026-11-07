"""One validated client-identity rule for trusted ingress peers.

Django only accepts the canonical ``X-Client-IP`` header from a peer inside the
configured internal proxy network (the Nginx hop). Cloudflare handling happens
at Nginx, which rewrites ``$remote_addr`` only for published Cloudflare ranges
and overwrites the forwarded identity headers toward Django. A direct caller
cannot spoof its identity with CF-Connecting-IP, X-Forwarded-For or X-Real-IP.
"""

from __future__ import annotations

import ipaddress
from typing import Iterable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _parse_ip(value: object):
    """Return an ``ipaddress`` object, or ``None`` for missing/invalid input."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or "%" in candidate:
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    # A mapped IPv4 peer (::ffff:172.16.0.2) must match IPv4 networks.
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def trusted_proxy_networks() -> tuple:
    """Return the configured trusted ingress networks as ``ip_network`` objects."""

    configured: Iterable[str] = getattr(settings, "TRUSTED_PROXY_IPS", ())
    networks = []
    for value in configured:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ImproperlyConfigured(
                f"DJANGO_TRUSTED_PROXY_IPS contains an invalid network: {value!r}"
            ) from exc
    return tuple(networks)


def _peer_is_trusted(peer) -> bool:
    return any(peer in network for network in trusted_proxy_networks())


def client_ip(request) -> str:
    """Return the canonical client address for limits and diagnostics."""

    peer = _parse_ip(request.META.get("REMOTE_ADDR"))
    if peer is None:
        return ""
    if not _peer_is_trusted(peer):
        return str(peer)
    forwarded = _parse_ip(request.META.get("HTTP_X_CLIENT_IP"))
    return str(forwarded) if forwarded is not None else str(peer)

"""Which host a request was sent to, and whether it may be served (ADR-0012).

The app answers only for names on ``FINANCE360_ALLOWED_HOSTS``. Behind the Caddy sidecar the name
the browser typed is in ``X-Forwarded-Host``, which Caddy overwrites on every request; it is
honoured only when the TCP peer is the proxy, so a client that reaches the app by any other route
cannot choose its own host.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Iterable, Mapping

#: The compose service name of the sidecar (docker-compose.yml). Not configurable on purpose.
PROXY_SERVICE = "proxy"
RESOLVE_TTL = 5.0


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """True if ``host`` (``name`` or ``name:port``) matches an entry; a portless entry any port."""
    host = host.strip().lower()
    name = host.partition(":")[0]
    return any(host == a or (":" not in a and name == a) for a in allowed)


def _normal(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return address
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped or ip)


class ProxyPeers:
    """The addresses of the ``proxy`` compose service, resolved through the compose network's DNS.

    Cached for a few seconds so a restarted proxy (new address) is picked up. Outside compose the
    name does not resolve, the set is empty, and forwarded headers are never honoured.
    """

    def __init__(
        self,
        name: str = PROXY_SERVICE,
        *,
        ttl: float = RESOLVE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name, self._ttl, self._clock = name, ttl, clock
        self._addresses: frozenset[str] = frozenset()
        self._expires = 0.0

    def __call__(self) -> frozenset[str]:
        if self._clock() >= self._expires:
            try:
                found: set[str] = {
                    _normal(str(i[4][0])) for i in socket.getaddrinfo(self._name, None)
                }
            except OSError:
                found = set()
            self._addresses, self._expires = frozenset(found), self._clock() + self._ttl
        return self._addresses


def effective_host(
    headers: Mapping[str, str], peer: str | None, proxy_addresses: Callable[[], frozenset[str]]
) -> str:
    """The first ``X-Forwarded-Host`` if the peer is the proxy, else the ``Host`` header."""
    forwarded = headers.get("x-forwarded-host", "").split(",")[0].strip()
    if forwarded and peer and _normal(peer) in proxy_addresses():
        return forwarded.lower()
    return headers.get("host", "").strip().lower()

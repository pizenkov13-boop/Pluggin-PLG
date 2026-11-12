"""HTTP client for the cloud API with a DNS-over-HTTPS *fallback*.

Some networks hand out a wrong IP for a domain (an antivirus, an ISP, a broken
local resolver) while a browser with its own DoH reaches the site fine. The
system resolver stays first — nothing changes for anyone whose connection works.
Only when connecting fails (refused, timed out, name not found) do we ask a DoH
provider for the address and try that, Cloudflare then Google, and if that does
not help either, the original error is raised untouched.

TLS is unaffected: httpcore passes only the *host* to ``connect_tcp`` and takes
the SNI / certificate name from the URL, so a connection made to a DoH-supplied
IP is still verified against the PLG server's own host name.

Known trade-off (see the project issue log (not included)): DoH resolvers are addressed by literal IP
(1.1.1.1, 8.8.8.8 — they must be reachable when DNS is not), and an unsigned exe
that talks DoH to hard-coded IPs resembles DNS-bypass malware to some antivirus
heuristics. ``PLG_DOH=0`` switches the whole thing off.

Relies on httpx's private ``HTTPTransport._pool._network_backend``; httpx and
httpcore are pinned in requirements.txt for that reason, and ``make_client``
degrades to a plain client if the attribute is ever missing.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time
from typing import Any, Callable

import httpcore
import httpx

logger = logging.getLogger("plg.net")

# (label, url). The hosts are literal IPs on purpose: DoH is only useful when
# name resolution is what is broken. Both certificates carry IP SANs.
DOH_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("cloudflare", "https://1.1.1.1/dns-query"),
    ("google", "https://8.8.8.8/resolve"),
)
DOH_REQUEST_TIMEOUT_S = 1.5
DOH_BREAKER_PAUSE_S = 600.0  # after every provider failed, leave DoH alone this long
DOH_CACHE_MAX_TTL_S = 600.0
MAX_DOH_ADDRESSES = 3  # never let a long A-record list stretch one failed request

# Never sent through DoH: the resolvers themselves (avoids recursion) and names
# that are local by definition.
_NO_DOH_HOSTS = frozenset({"localhost", "1.1.1.1", "8.8.8.8", "one.one.one.one", "cloudflare-dns.com", "dns.google"})

Fetch = Callable[[str, str, float], Any]


def doh_enabled() -> bool:
    return (os.getenv("PLG_DOH") or "1").strip().lower() not in ("0", "false", "no", "off")


def parse_doh_answer(payload: Any) -> list[tuple[str, float]]:
    """``[(ipv4, ttl_seconds)]`` from an ``application/dns-json`` reply.

    Only A records count: a CNAME chain is skipped (the A records at its end are
    in the same ``Answer`` list), and anything malformed yields ``[]``.
    """
    if not isinstance(payload, dict) or payload.get("Status") not in (0, None):
        return []
    answers = payload.get("Answer")
    if not isinstance(answers, list):
        return []
    found: list[tuple[str, float]] = []
    for record in answers:
        if not isinstance(record, dict) or record.get("type") != 1:
            continue
        try:
            address = str(ipaddress.IPv4Address(str(record.get("data") or "").strip()))
            ttl = float(record.get("TTL", DOH_CACHE_MAX_TTL_S))
        except (ValueError, TypeError):
            continue
        found.append((address, max(0.0, ttl)))
    return found


def _fetch_json(url: str, name: str, timeout: float) -> Any:
    from local_only import require_network

    require_network("a DNS-over-HTTPS lookup")
    # A plain client on purpose: the DoH request must never go through DohBackend.
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.get(url, params={"name": name, "type": "A"}, headers={"accept": "application/dns-json"})
        response.raise_for_status()
        return response.json()


class DohResolver:
    """Cached, circuit-broken DoH lookups. ``fetch`` and ``clock`` are injectable."""

    def __init__(
        self,
        *,
        providers: tuple[tuple[str, str], ...] = DOH_PROVIDERS,
        fetch: Fetch = _fetch_json,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._paused_until = 0.0

    def resolve(self, host: str) -> list[str]:
        """IPv4 addresses for ``host``, or ``[]`` (disabled, paused, or unknown)."""
        if not doh_enabled():
            return []
        key = host.lower()
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return list(cached[1])
            if now < self._paused_until:
                return []
        answered = False
        for label, url in self._providers:
            try:
                records = parse_doh_answer(self._fetch(url, key, DOH_REQUEST_TIMEOUT_S))
            except Exception as exc:  # noqa: BLE001 - any provider failure just moves on
                logger.info("DoH %s failed for %s: %s", label, key, type(exc).__name__)
                continue
            answered = True
            if records:
                ttl = min(min(ttl for _ip, ttl in records), DOH_CACHE_MAX_TTL_S)
                addresses = [ip for ip, _ttl in records]
                with self._lock:
                    self._cache[key] = (self._clock() + ttl, addresses)
                return list(addresses)
            # A provider that answers with no A record is a real "no such host",
            # not an outage: do not pause DoH for it.
            return []
        if not answered:
            with self._lock:
                self._paused_until = self._clock() + DOH_BREAKER_PAUSE_S
            logger.warning("DoH unreachable; not trying it again for %d s", int(DOH_BREAKER_PAUSE_S))
        return []


_default_resolver = DohResolver()


def _skips_doh(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in _NO_DOH_HOSTS or lowered.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return False
    return True  # a literal address has nothing to resolve


class DohBackend(httpcore.NetworkBackend):
    """The system connection first; on a connect failure, the DoH addresses."""

    def __init__(self, resolver: DohResolver | None = None, inner: Any = None) -> None:
        self._resolver = resolver
        self._inner = inner if inner is not None else httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            return self._inner.connect_tcp(host, port, timeout, local_address, socket_options)
        except (httpcore.ConnectError, httpcore.ConnectTimeout) as original:
            if _skips_doh(host):
                raise
            resolver = self._resolver or _default_resolver
            for address in resolver.resolve(host)[:MAX_DOH_ADDRESSES]:
                try:
                    stream = self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout):
                    continue
                logger.info("Connected to %s through DoH address %s", host, address)
                return stream
            raise original

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return self._inner.connect_unix_socket(path, timeout, socket_options)

    def sleep(self, seconds):
        return self._inner.sleep(seconds)


def make_client(timeout: Any = None) -> httpx.Client:
    """``httpx.Client(timeout=..., trust_env=False)`` plus the DoH fallback.

    Falls back to exactly that plain client when ``PLG_DOH=0`` or if httpx's
    internals are not what this module expects — never a reason to fail a call.
    """
    from local_only import require_network

    require_network("the PLG server connection")  # the tester build never reaches the network
    if not doh_enabled():
        return httpx.Client(timeout=timeout, trust_env=False)
    try:
        transport = httpx.HTTPTransport(trust_env=False)
        pool = transport._pool  # type: ignore[attr-defined]
        if not hasattr(pool, "_network_backend"):  # plain assignment would silently create it
            raise AttributeError("_network_backend")
        pool._network_backend = DohBackend()
    except AttributeError:
        logger.warning("httpx internals changed; DoH fallback disabled")
        return httpx.Client(timeout=timeout, trust_env=False)
    return httpx.Client(timeout=timeout, trust_env=False, transport=transport)

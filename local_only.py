"""The tester build never touches the network: a guard at every client and a guard on the socket layer.

Two layers, because "no network call, ever" must not depend on every future call site remembering a check:

1. ``require_network(what)`` - the HTTP clients (``plg_net.make_client``, the LLM calls) call it first and get
   ``LocalOnlyError`` in a tester build.
2. ``install_socket_guard()`` - started by ``plg_webview.main`` in a tester build: every connection or name
   lookup that is not loopback raises ``LocalOnlyError`` (a ``ConnectionError``), whatever code makes it. The
   app's own pages (``127.0.0.1``: the UI server and the sound preview) keep working.

Python's sockets are not the whole process tree: the window is Microsoft's WebView2 (Edge) runtime, whose own
services talk to Microsoft on port 443 by themselves (measured on the first tester build: PLG_Tester.exe had no
outside connection, one msedgewebview2.exe utility process had). ``apply_webview_isolation()`` points that engine's
proxy at a closed local port and switches its background networking off, so it too can only reach loopback
(the app's pages are loopback and bypass the proxy).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable

_ALLOWED_NAMES = {"localhost", "localhost.localdomain"}


class LocalOnlyError(ConnectionError):
    """The tester build does not use the network."""


def network_forbidden() -> bool:
    from app_config import tester_build

    return tester_build()


def require_network(what: str = "a network call") -> None:
    """Raise ``LocalOnlyError`` in a tester build; does nothing anywhere else."""
    if network_forbidden():
        raise LocalOnlyError(f"{what} is turned off in the tester build")


def _is_loopback_host(host: Any) -> bool:
    if host is None or host == "":
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    text = str(host).strip().strip("[]").split("%", 1)[0].lower()
    if text in _ALLOWED_NAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _address_host(address: Any) -> Any:
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return address


#: Chromium switches for the WebView2 window of a tester build: no route out, no background services.
WEBVIEW2_ISOLATION_ARGS = (
    "--proxy-server=socks5://127.0.0.1:9",  # a closed local port: nothing leaves, loopback pages bypass it
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-pings",
)


def apply_webview_isolation(environ: dict | None = None) -> str:
    """Add the switches above to ``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`` (keeping anything already there)."""
    import os

    env = os.environ if environ is None else environ
    existing = env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").split()
    merged = existing + [arg for arg in WEBVIEW2_ISOLATION_ARGS if arg not in existing]
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(merged)
    return env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]


def install_socket_guard() -> Callable[[], None]:
    """Refuse every non-loopback connection, datagram and name lookup in this process.

    Returns a function that puts the original behaviour back (used by the tests)."""
    original = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
    }

    def refuse(host: Any) -> None:
        raise LocalOnlyError(f"connection to {host!r} is turned off in the tester build")

    def connect(self, address):  # noqa: ANN001
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_host(_address_host(address)):
            refuse(_address_host(address))
        return original["connect"](self, address)

    def connect_ex(self, address):  # noqa: ANN001
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_host(_address_host(address)):
            refuse(_address_host(address))
        return original["connect_ex"](self, address)

    def sendto(self, data, *args):  # noqa: ANN001
        address = args[-1] if args else None
        if address is not None and self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_host(_address_host(address)):
            refuse(_address_host(address))
        return original["sendto"](self, data, *args)

    def getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
        if not _is_loopback_host(host):
            refuse(host)
        return original["getaddrinfo"](host, *args, **kwargs)

    def gethostbyname(host):  # noqa: ANN001
        if not _is_loopback_host(host):
            refuse(host)
        return original["gethostbyname"](host)

    def gethostbyname_ex(host):  # noqa: ANN001
        if not _is_loopback_host(host):
            refuse(host)
        return original["gethostbyname_ex"](host)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex

    def uninstall() -> None:
        socket.socket.connect = original["connect"]
        socket.socket.connect_ex = original["connect_ex"]
        socket.socket.sendto = original["sendto"]
        socket.getaddrinfo = original["getaddrinfo"]
        socket.gethostbyname = original["gethostbyname"]
        socket.gethostbyname_ex = original["gethostbyname_ex"]

    return uninstall

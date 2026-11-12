"""The tester build never touches the network: the client guards and the socket guard (loopback stays open)."""

from __future__ import annotations

import socket
import threading

import pytest

import app_config
import local_only
from local_only import LocalOnlyError

DOC_ADDRESS = "203.0.113.9"  # documentation range: nothing is ever sent, the guard refuses first


@pytest.fixture
def tester(monkeypatch):
    monkeypatch.setattr(app_config, "tester_build", lambda: True)


@pytest.fixture
def guard():
    uninstall = local_only.install_socket_guard()
    try:
        yield
    finally:
        uninstall()


# --- layer 1: the clients ------------------------------------------------------------------


def test_require_network_only_refuses_in_a_tester_build(monkeypatch):
    monkeypatch.setattr(app_config, "tester_build", lambda: False)
    local_only.require_network("x")  # nothing happens
    monkeypatch.setattr(app_config, "tester_build", lambda: True)
    with pytest.raises(LocalOnlyError):
        local_only.require_network("x")


def test_the_http_clients_refuse_before_opening_anything(tester, monkeypatch):
    import bass808_llm
    import llm_client
    import plg_net

    with pytest.raises(LocalOnlyError):
        plg_net.make_client(timeout=1.0)
    with pytest.raises(LocalOnlyError):
        plg_net._fetch_json("https://doh.invalid/dns-query", "example.test", 1.0)
    with pytest.raises(LocalOnlyError):
        llm_client._openrouter_json("p", "s", {"type": "object"}, schema_name="x", temperature=0.1, max_tokens=10)
    with pytest.raises(LocalOnlyError):
        llm_client.openrouter_json_object("p", "s", model="m", temperature=0.1, max_tokens=10)
    with pytest.raises(LocalOnlyError):
        bass808_llm._call_llm("p")


def test_outside_a_tester_build_the_client_is_made(monkeypatch):
    import plg_net

    monkeypatch.setattr(app_config, "tester_build", lambda: False)
    with plg_net.make_client(timeout=1.0):
        pass


# --- layer 2: the sockets ------------------------------------------------------------------


def test_a_connection_to_anything_but_loopback_is_refused_and_loopback_still_works(guard):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted = []
    threading.Thread(target=lambda: accepted.append(server.accept()), daemon=True).start()

    with socket.create_connection(("127.0.0.1", port), timeout=3):
        pass  # the app's own pages are served from here
    with pytest.raises(LocalOnlyError):
        socket.create_connection((DOC_ADDRESS, 80), timeout=1)
    with pytest.raises(LocalOnlyError):
        socket.socket().connect((DOC_ADDRESS, 80))
    with pytest.raises(LocalOnlyError):
        socket.socket().connect_ex((DOC_ADDRESS, 80))
    server.close()


def test_a_name_lookup_for_a_real_host_is_refused_but_localhost_resolves(guard):
    for host in ("example.com", "cloud.example.test", "8.8.8.8"):
        with pytest.raises(LocalOnlyError):
            socket.getaddrinfo(host, 443)
    with pytest.raises(LocalOnlyError):
        socket.gethostbyname("example.com")
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_a_datagram_to_the_outside_is_refused(guard):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with pytest.raises(LocalOnlyError):
        sock.sendto(b"x", (DOC_ADDRESS, 53))
    sock.sendto(b"x", ("127.0.0.1", 9))  # loopback is fine (nobody needs to listen)
    sock.close()


def test_an_http_library_cannot_get_out_either(guard):
    import httpx

    with pytest.raises(Exception) as info:
        httpx.get(f"http://{DOC_ADDRESS}/", timeout=2)
    chain, error = [], info.value
    while error is not None:
        chain.append(error)
        error = error.__cause__ or error.__context__
    assert any(isinstance(e, LocalOnlyError) for e in chain)


def test_uninstall_puts_the_original_behaviour_back():
    before = socket.getaddrinfo
    uninstall = local_only.install_socket_guard()
    assert socket.getaddrinfo is not before
    uninstall()
    assert socket.getaddrinfo is before


# --- the browser engine of the window ------------------------------------------------------


def test_the_webview_engine_is_cut_off_from_everything_but_loopback():
    env: dict = {}
    value = local_only.apply_webview_isolation(env)
    assert value == env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    for switch in ("--proxy-server=socks5://127.0.0.1:9", "--disable-background-networking", "--disable-component-update"):
        assert switch in value.split()


def test_existing_webview_arguments_are_kept_and_nothing_is_added_twice():
    env = {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--lang=ru --no-pings"}
    first = local_only.apply_webview_isolation(env)
    assert first.split().count("--no-pings") == 1 and "--lang=ru" in first.split()
    assert local_only.apply_webview_isolation(env) == first


def test_the_release_start_does_not_touch_the_webview_arguments():
    source = (__import__("pathlib").Path(__file__).resolve().parent.parent / "plg_webview.py").read_text(encoding="utf-8")
    block = source[source.index("if tester_build():"):]
    assert block.index("apply_webview_isolation()") < block.index("load_environment()")  # inside the tester branch, before the app starts
    assert source.count("apply_webview_isolation") == 2  # the import and the one call, both under the tester check

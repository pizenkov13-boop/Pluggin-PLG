"""DNS-over-HTTPS as a fallback, never the first choice (plg_net).

The system resolver stays first; DoH is asked only after a connect failure, and
whatever happens the caller sees either a working connection or the original
error. No test touches the real network.
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
from pathlib import Path

import httpcore
import httpx
import pytest

import plg_net
from plg_net import DohBackend, DohResolver, make_client, parse_doh_answer

CF = ("cloudflare", "https://1.1.1.1/dns-query")
GG = ("google", "https://8.8.8.8/resolve")


def answer(*records, status=0):
    return {"Status": status, "Answer": list(records)}


def a(ip, ttl=300):
    return {"name": "x.", "type": 1, "TTL": ttl, "data": ip}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Fetch:
    """Scripted DoH provider: url -> payload or exception; records every call."""

    def __init__(self, script):
        self.script = script
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, name, timeout):
        self.calls.append((url, name))
        outcome = self.script[url]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def resolver(script, clock=None):
    fetch = Fetch(script)
    return DohResolver(providers=(CF, GG), fetch=fetch, clock=clock or Clock()), fetch


@pytest.fixture(autouse=True)
def _doh_on(monkeypatch):
    monkeypatch.delenv("PLG_DOH", raising=False)


# --- parsing ----------------------------------------------------------------------------


def test_only_a_records_count_and_a_cname_chain_is_skipped():
    payload = answer(
        {"name": "cloud.example.", "type": 5, "TTL": 60, "data": "edge.example."},
        a("203.0.113.7", 120),
        {"name": "edge.example.", "type": 28, "TTL": 60, "data": "2001:db8::1"},
        a("203.0.113.8", 30),
    )
    assert parse_doh_answer(payload) == [("203.0.113.7", 120.0), ("203.0.113.8", 30.0)]


@pytest.mark.parametrize(
    "payload",
    [
        {"Status": 0},  # no Answer at all
        answer(),  # empty Answer
        answer(status=3),  # NXDOMAIN
        {"Status": 0, "Answer": "nope"},
        answer(a("not-an-ip"), a("999.1.1.1"), a("")),
        "<html>captive portal</html>",  # not JSON-shaped
        ["a"],
        None,
    ],
)
def test_nothing_usable_in_the_reply_gives_an_empty_list(payload):
    assert parse_doh_answer(payload) == []


# --- cache --------------------------------------------------------------------------------


def test_a_second_lookup_is_served_from_the_cache():
    res, fetch = resolver({CF[1]: answer(a("203.0.113.7", 300))})
    assert res.resolve("Cloud.Example") == ["203.0.113.7"]
    assert res.resolve("cloud.example") == ["203.0.113.7"]
    assert len(fetch.calls) == 1


def test_the_cache_entry_expires_with_its_ttl():
    clock = Clock()
    res, fetch = resolver({CF[1]: answer(a("203.0.113.7", 60))}, clock)
    res.resolve("h.example")
    clock.now += 59
    res.resolve("h.example")
    assert len(fetch.calls) == 1
    clock.now += 2
    res.resolve("h.example")
    assert len(fetch.calls) == 2


def test_a_long_ttl_is_capped_at_ten_minutes():
    clock = Clock()
    res, fetch = resolver({CF[1]: answer(a("203.0.113.7", 86400))}, clock)
    res.resolve("h.example")
    clock.now += 599
    res.resolve("h.example")
    assert len(fetch.calls) == 1
    clock.now += 2  # 601 s, though the record says a day
    res.resolve("h.example")
    assert len(fetch.calls) == 2


# --- providers and the circuit breaker ----------------------------------------------------


def test_cloudflare_is_asked_first_and_google_only_when_it_fails():
    res, fetch = resolver({CF[1]: httpx.ConnectTimeout("blocked"), GG[1]: answer(a("198.51.100.4"))})
    assert res.resolve("h.example") == ["198.51.100.4"]
    assert [url for url, _ in fetch.calls] == [CF[1], GG[1]]


def test_google_is_not_asked_when_cloudflare_answers():
    res, fetch = resolver({CF[1]: answer(a("198.51.100.4")), GG[1]: AssertionError("must not be called")})
    assert res.resolve("h.example") == ["198.51.100.4"]
    assert [url for url, _ in fetch.calls] == [CF[1]]


def test_after_every_provider_fails_doh_is_left_alone_for_ten_minutes_then_resumes():
    clock = Clock()
    down = {CF[1]: httpx.ConnectTimeout("x"), GG[1]: httpx.ConnectError("x")}
    res, fetch = resolver(down, clock)
    assert res.resolve("h.example") == []
    assert len(fetch.calls) == 2
    clock.now += 599
    assert res.resolve("other.example") == []
    assert len(fetch.calls) == 2  # paused: nobody is asked
    clock.now += 2
    fetch.script = {CF[1]: answer(a("198.51.100.4")), GG[1]: answer()}
    assert res.resolve("h.example") == ["198.51.100.4"]
    assert len(fetch.calls) == 3


def test_a_provider_saying_there_is_no_such_host_is_not_an_outage():
    res, fetch = resolver({CF[1]: answer(status=3), GG[1]: AssertionError("must not be called")})
    assert res.resolve("nope.example") == []
    fetch.script = {CF[1]: answer(a("198.51.100.4")), GG[1]: answer()}
    assert res.resolve("real.example") == ["198.51.100.4"]  # not paused


def test_a_captive_portal_page_counts_as_an_answer_without_addresses():
    res, _ = resolver({CF[1]: "<html>captive portal</html>", GG[1]: AssertionError("must not be called")})
    assert res.resolve("h.example") == []


def test_plg_doh_off_never_calls_a_provider(monkeypatch):
    monkeypatch.setenv("PLG_DOH", "0")
    res, fetch = resolver({CF[1]: AssertionError("must not be called"), GG[1]: AssertionError("must not be called")})
    assert res.resolve("h.example") == []
    assert fetch.calls == []


def test_the_doh_request_itself_is_a_plain_client_with_the_right_query(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["accept"] = request.url, request.headers.get("accept")
        return httpx.Response(200, json=answer(a("198.51.100.4")))

    real = httpx.Client

    def factory(**kwargs):
        assert "transport" not in kwargs, "the DoH lookup must not run through DohBackend"
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(plg_net.httpx, "Client", factory)
    assert plg_net._fetch_json(CF[1], "cloud.example", 1.5)["Answer"][0]["data"] == "198.51.100.4"
    assert seen["accept"] == "application/dns-json"
    assert dict(seen["url"].params) == {"name": "cloud.example", "type": "A"}


# --- the backend -------------------------------------------------------------------------------


class FakeInner:
    """Stands in for the system connection: only ``working`` hosts connect."""

    def __init__(self, working=(), error=None):
        self.working = set(working)
        self.error = error or httpcore.ConnectError("system DNS is wrong")
        self.attempts: list[str] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.attempts.append(host)
        if host in self.working:
            return f"stream:{host}"
        raise self.error

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return f"unix:{path}"

    def sleep(self, seconds):
        self.slept = seconds


def backend(inner, script=None):
    if script is None:
        script = {CF[1]: answer(a("198.51.100.4"), a("198.51.100.5")), GG[1]: answer()}
    res, fetch = resolver(script)
    return DohBackend(resolver=res, inner=inner), fetch


def test_a_working_system_connection_never_touches_doh():
    inner = FakeInner(working={"cloud.example"})
    be, fetch = backend(inner)
    assert be.connect_tcp("cloud.example", 443) == "stream:cloud.example"
    assert fetch.calls == [] and inner.attempts == ["cloud.example"]


def test_a_failed_system_connection_falls_back_to_the_doh_address():
    inner = FakeInner(working={"198.51.100.4"})
    be, _ = backend(inner)
    assert be.connect_tcp("cloud.example", 443) == "stream:198.51.100.4"
    assert inner.attempts == ["cloud.example", "198.51.100.4"]


def test_a_connect_timeout_also_triggers_the_fallback():
    inner = FakeInner(working={"198.51.100.4"}, error=httpcore.ConnectTimeout("timed out"))
    be, _ = backend(inner)
    assert be.connect_tcp("cloud.example", 443) == "stream:198.51.100.4"


def test_the_second_doh_address_is_tried_when_the_first_does_not_answer():
    inner = FakeInner(working={"198.51.100.5"})
    be, _ = backend(inner)
    assert be.connect_tcp("cloud.example", 443) == "stream:198.51.100.5"
    assert inner.attempts == ["cloud.example", "198.51.100.4", "198.51.100.5"]


def test_when_nothing_works_the_original_error_is_raised_untouched():
    original = httpcore.ConnectError("getaddrinfo failed")
    inner = FakeInner(error=original)
    be, _ = backend(inner)
    with pytest.raises(httpcore.ConnectError) as caught:
        be.connect_tcp("cloud.example", 443)
    assert caught.value is original


def test_when_doh_has_no_answer_either_the_original_error_is_raised():
    original = httpcore.ConnectError("getaddrinfo failed")
    be, _ = backend(FakeInner(error=original), {CF[1]: httpx.ConnectError("x"), GG[1]: httpx.ConnectError("x")})
    with pytest.raises(httpcore.ConnectError) as caught:
        be.connect_tcp("cloud.example", 443)
    assert caught.value is original


def test_at_most_three_doh_addresses_are_tried():
    ips = [a(f"198.51.100.{n}") for n in range(10, 16)]
    inner = FakeInner()
    be, _ = backend(inner, {CF[1]: answer(*ips), GG[1]: answer()})
    with pytest.raises(httpcore.ConnectError):
        be.connect_tcp("cloud.example", 443)
    assert len(inner.attempts) == 1 + plg_net.MAX_DOH_ADDRESSES


@pytest.mark.parametrize(
    "host",
    ["203.0.113.9", "127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST", "api.localhost", "1.1.1.1", "8.8.8.8", "dns.google", "cloudflare-dns.com"],
)
def test_literals_localhost_and_the_resolvers_themselves_bypass_doh(host):
    inner = FakeInner()
    be, fetch = backend(inner, {CF[1]: AssertionError("must not be called"), GG[1]: AssertionError("must not be called")})
    with pytest.raises(httpcore.ConnectError):
        be.connect_tcp(host, 443)
    assert fetch.calls == [] and inner.attempts == [host]


def test_with_plg_doh_off_a_failed_connection_stays_failed(monkeypatch):
    monkeypatch.setenv("PLG_DOH", "0")
    inner = FakeInner(working={"198.51.100.4"})
    be, fetch = backend(inner, {CF[1]: AssertionError("must not be called"), GG[1]: AssertionError("must not be called")})
    with pytest.raises(httpcore.ConnectError):
        be.connect_tcp("cloud.example", 443)
    assert fetch.calls == []


def test_unix_sockets_and_sleep_pass_straight_through():
    inner = FakeInner()
    be, _ = backend(inner)
    assert be.connect_unix_socket("/tmp/s") == "unix:/tmp/s"
    be.sleep(0.25)
    assert inner.slept == 0.25


# --- make_client ---------------------------------------------------------------------------------


def test_make_client_installs_the_backend_and_keeps_timeout_and_trust_env_off():
    client = make_client(timeout=httpx.Timeout(12.0, connect=4.0))
    try:
        assert isinstance(client._transport._pool._network_backend, DohBackend)
        assert client.timeout == httpx.Timeout(12.0, connect=4.0)
        assert client.trust_env is False
    finally:
        client.close()


def test_make_client_with_plg_doh_off_is_a_plain_client(monkeypatch):
    monkeypatch.setenv("PLG_DOH", "0")
    client = make_client(timeout=6.0)
    try:
        assert not isinstance(client._transport._pool._network_backend, DohBackend)
        assert client.trust_env is False and client.timeout == httpx.Timeout(6.0)
    finally:
        client.close()


def test_make_client_survives_httpx_internals_changing(monkeypatch):
    class NoPool:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr(plg_net.httpx, "HTTPTransport", NoPool)
    client = make_client(timeout=3.0)  # falls back instead of raising
    assert isinstance(client, httpx.Client)
    client.close()


# --- integration: a real socket, a scripted DoH, no internet ------------------------------------------


class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"reached through DoH"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _break_system_dns(monkeypatch, host):
    real = socket.getaddrinfo

    def broken(name, *args, **kwargs):
        if name == host:
            raise socket.gaierror(11001, "getaddrinfo failed")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", broken)


def test_end_to_end_the_wrong_system_dns_is_bypassed_through_doh(local_server, monkeypatch):
    host = "cloud-doh-test.example"
    _break_system_dns(monkeypatch, host)
    fetch = Fetch({CF[1]: answer(a("127.0.0.1")), GG[1]: answer()})
    monkeypatch.setattr(plg_net, "_default_resolver", DohResolver(providers=(CF, GG), fetch=fetch))
    with make_client(timeout=5.0) as client:
        response = client.get(f"http://{host}:{local_server}/health")
    assert response.status_code == 200 and response.text == "reached through DoH"
    assert [name for _url, name in fetch.calls] == [host]


def test_end_to_end_without_a_doh_answer_the_caller_gets_the_connect_error(local_server, monkeypatch):
    host = "cloud-doh-test.example"
    _break_system_dns(monkeypatch, host)
    down = {CF[1]: httpx.ConnectTimeout("x"), GG[1]: httpx.ConnectTimeout("x")}
    monkeypatch.setattr(plg_net, "_default_resolver", DohResolver(providers=(CF, GG), fetch=Fetch(down)))
    with make_client(timeout=5.0) as client, pytest.raises(httpx.ConnectError):
        client.get(f"http://{host}:{local_server}/health")


def test_end_to_end_a_working_connection_never_asks_doh(local_server, monkeypatch):
    fetch = Fetch({CF[1]: AssertionError("must not be called"), GG[1]: AssertionError("must not be called")})
    monkeypatch.setattr(plg_net, "_default_resolver", DohResolver(providers=(CF, GG), fetch=fetch))
    with make_client(timeout=5.0) as client:
        assert client.get(f"http://127.0.0.1:{local_server}/").status_code == 200
    assert fetch.calls == []


# --- guards ------------------------------------------------------------------------------------------------


ROOT = Path(__file__).resolve().parent.parent


def test_plg_cloud_has_no_direct_httpx_client_left():
    source = (ROOT / "plg_cloud.py").read_text(encoding="utf-8")
    assert not re.search(r"httpx\.Client\(", source)
    assert len(re.findall(r"\bmake_client\(", source)) == 14


def test_the_private_httpx_field_the_backend_relies_on_exists():
    pool = httpx.HTTPTransport(trust_env=False)._pool
    assert hasattr(pool, "_network_backend")
    assert isinstance(pool._network_backend, httpcore.NetworkBackend)
    assert callable(getattr(httpcore.NetworkBackend, "connect_tcp", None))


def test_httpx_and_httpcore_are_pinned_to_what_is_installed():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8").replace(" ", "")
    assert f"httpx=={httpx.__version__}" in text
    assert f"httpcore=={httpcore.__version__}" in text

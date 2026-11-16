"""Tests for the web page and the JSON API."""

import contextlib

import dns.message
import dns.query
import dns.rcode
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from ip import dnsleak, main

TEST_ID = "0123456789abcdef0123456789abcdef"

GOOGLE = {
    "country": "United States",
    "iso_code": "US",
    "city": "Unknown",
    "ip": "8.8.8.8",
    "version": 4,
    "org": "Google LLC",
}


def from_ip(ip: str) -> dict[str, str]:
    """Headers Caddy sends for a request from `ip`."""
    return {"X-Forwarded-For": ip}


def make_request(headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    raw_headers = [
        (name.lower().encode(), value.encode()) for name, value in headers.items()
    ]
    return Request({"type": "http", "headers": raw_headers, "client": client})


def location(ip: str, org: str) -> main.ClientLocationResponse:
    return main.ClientLocationResponse(
        country="Unknown", iso_code="Unknown", city="Unknown", ip=ip, version=4, org=org
    )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-Forwarded-For": "8.8.8.8, 10.0.0.1"}, "8.8.8.8"),
        ({"X-Forwarded-For": " 8.8.8.8 "}, "8.8.8.8"),
        ({"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "8.8.4.4"}, "8.8.8.8"),
        ({"X-Real-IP": "8.8.4.4"}, "8.8.4.4"),
        ({}, "172.18.0.2"),
    ],
)
def test_get_client_ip(headers, expected):
    assert main.get_client_ip(make_request(headers, ("172.18.0.2", 443))) == expected


def test_get_client_ip_without_any_address():
    with pytest.raises(HTTPException) as excinfo:
        main.get_client_ip(make_request({}, None))
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("8.8.8.8", "8.8.8.8"),
        ("2001:4860:4860::8888", "2001:4860:4860::8888"),
        ("2001:4860:4860:0::8888", "2001:4860:4860::8888"),
        ("::ffff:8.8.8.8", "8.8.8.8"),
    ],
)
def test_parse_ip(ip, expected):
    assert str(main.parse_ip(ip)) == expected


def test_parse_ip_rejects_invalid_address():
    with pytest.raises(HTTPException) as excinfo:
        main.parse_ip("8.8.8")
    assert excinfo.value.status_code == 400


def test_location_of_given_ip(client):
    response = client.get("/api/ip/8.8.4.4/")
    assert response.status_code == 200
    assert response.json() == {**GOOGLE, "ip": "8.8.4.4", "city": "Westfield"}


def test_location_of_ipv6_address(client):
    response = client.get("/api/ip/2001:4860:4860::8888/")
    assert response.json() == {**GOOGLE, "ip": "2001:4860:4860::8888", "version": 6}


def test_location_unwraps_ipv4_mapped_address(client):
    assert client.get("/api/ip/::ffff:8.8.8.8/").json() == GOOGLE


def test_location_fills_missing_fields_with_unknown(client, geoip):
    geoip.add("203.0.113.7", org=None)
    assert client.get("/api/ip/203.0.113.7/").json() == {
        "country": "Unknown",
        "iso_code": "Unknown",
        "city": "Unknown",
        "ip": "203.0.113.7",
        "version": 4,
        "org": "Unknown",
    }


def test_location_of_invalid_address(client):
    response = client.get("/api/ip/not-an-ip/")
    assert response.status_code == 400
    assert response.json() == {"detail": "not-an-ip is not a valid IP address."}


def test_location_of_address_not_in_database(client):
    response = client.get("/api/ip/192.0.2.1/")
    assert response.status_code == 404
    assert response.json() == {
        "detail": "IP address 192.0.2.1 not found in GeoIP database."
    }


def test_location_without_database(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "GEOIP_ASN_DB_PATH", str(tmp_path / "missing.mmdb"))
    response = client.get("/api/ip/8.8.8.8/")
    assert response.status_code == 500
    assert response.json() == {"detail": "GeoIP database not found."}


def test_location_lookup_failure_is_logged_without_address(client, geoip, capsys):
    geoip.errors["8.8.8.8"] = ValueError("corrupt database")
    response = client.get("/api/ip/8.8.8.8/")
    assert response.status_code == 500
    assert response.json() == {
        "detail": "An error occurred while processing the request."
    }
    log = capsys.readouterr().out
    assert "corrupt database" in log
    assert "8.8.8.8" not in log


def test_location_of_requester(client):
    assert client.get("/api/ip/", headers=from_ip("8.8.8.8")).json() == GOOGLE


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("192.0.2.1", {"ip": "192.0.2.1", "version": 4}),
        ("2001:db8::1", {"ip": "2001:db8::1", "version": 6}),
        ("::ffff:192.0.2.1", {"ip": "192.0.2.1", "version": 4}),
    ],
)
def test_address_of_requester_needs_no_database(client, ip, expected):
    response = client.get("/api/address/", headers=from_ip(ip))
    assert response.status_code == 200
    assert response.json() == expected


def test_address_allows_other_origins(client):
    response = client.get(
        "/api/address/",
        headers={**from_ip("192.0.2.1"), "Origin": "https://ipv4.example.test"},
    )
    assert response.headers["access-control-allow-origin"] == "*"


def test_home_page_shows_location(client):
    response = client.get("/", headers=from_ip("8.8.4.4"))
    assert response.status_code == 200
    assert "8.8.4.4" in response.text
    assert "Westfield" in response.text
    assert 'id="dns-leak"' not in response.text


def test_home_page_runs_dns_leak_test_when_enabled(client, resolver_store):
    response = client.get("/", headers=from_ip("8.8.8.8"))
    assert f'data-zone="{main.DNS_ZONE}"' in response.text


def test_home_page_shows_error(client):
    response = client.get("/", headers=from_ip("192.0.2.1"))
    assert response.status_code == 404
    assert "IP address 192.0.2.1 not found in GeoIP database." in response.text


def test_favicon_redirects_to_static_file(client):
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/static/favicon.ico"
    assert client.get("/favicon.ico").status_code == 200


@pytest.mark.parametrize(
    ("resolver", "client_location", "expected"),
    [
        (location("8.8.8.8", "Google LLC"), location("8.8.4.4", "Google LLC"), True),
        (location("1.1.1.1", "Cloudflare"), location("8.8.4.4", "Google LLC"), False),
        (location("8.8.8.8", "Unknown"), location("8.8.4.4", "Google LLC"), None),
        (location("8.8.8.8", "Google LLC"), location("8.8.4.4", "Unknown"), None),
        (location("192.0.2.1", "Unknown"), location("192.0.2.1", "Unknown"), True),
        (location("8.8.8.8", "Google LLC"), None, None),
    ],
)
def test_same_network(resolver, client_location, expected):
    assert main.same_network(resolver, client_location) == expected


def test_dns_leak_test_turned_off(client):
    response = client.get(f"/api/dns/{TEST_ID}/")
    assert response.status_code == 404


@pytest.mark.parametrize("test_id", ["abc", TEST_ID.upper(), TEST_ID + "0"])
def test_dns_leak_test_rejects_bad_id(client, resolver_store, test_id):
    assert client.get(f"/api/dns/{test_id}/").status_code == 422


def test_dns_leak_test_lists_resolvers(client, resolver_store):
    resolver_store.add(TEST_ID, "8.8.8.8")
    resolver_store.add(TEST_ID, "192.0.2.53")
    response = client.get(f"/api/dns/{TEST_ID}/", headers=from_ip("8.8.4.4"))
    assert response.status_code == 200
    assert response.json() == {
        "leak": None,
        "resolvers": [
            {**GOOGLE, "same_network": True},
            {
                "country": "Unknown",
                "iso_code": "Unknown",
                "city": "Unknown",
                "ip": "192.0.2.53",
                "version": 4,
                "org": "Unknown",
                "same_network": None,
            },
        ],
    }


@pytest.mark.parametrize(
    ("requester", "resolvers", "expected_leak"),
    [
        ("8.8.4.4", [], None),
        ("8.8.4.4", ["8.8.8.8"], False),
        ("8.8.4.4", ["8.8.8.8", "1.1.1.1"], True),
        ("8.8.4.4", ["1.1.1.1", "192.0.2.53"], True),
        ("8.8.4.4", ["8.8.8.8", "192.0.2.53"], None),
        ("192.0.2.1", ["8.8.8.8"], None),
    ],
)
def test_dns_leak_verdict(client, resolver_store, requester, resolvers, expected_leak):
    for ip in resolvers:
        resolver_store.add(TEST_ID, ip)
    response = client.get(f"/api/dns/{TEST_ID}/", headers=from_ip(requester))
    assert response.json()["leak"] is expected_leak


def test_dns_leak_test_end_to_end(monkeypatch, resolver_store):
    """
    Starts the app with its DNS server, looks up a test name over UDP, and
    reads the resolver back from the API.
    """
    ports = []
    real_serve = dnsleak.serve

    @contextlib.asynccontextmanager
    async def serve_and_record_port(zone, port):
        async with real_serve(zone, port) as bound_port:
            ports.append(bound_port)
            yield bound_port

    monkeypatch.setattr(dnsleak, "serve", serve_and_record_port)

    with TestClient(main.app) as client:
        query = dns.message.make_query(f"{TEST_ID}-0.{main.DNS_ZONE}", "A")
        answer = dns.query.udp(query, "127.0.0.1", port=ports[0], timeout=2)
        response = client.get(f"/api/dns/{TEST_ID}/", headers=from_ip("8.8.8.8"))

    assert answer.rcode() == dns.rcode.NXDOMAIN
    assert [r["ip"] for r in response.json()["resolvers"]] == ["127.0.0.1"]

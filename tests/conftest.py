"""
Shared fixtures. Every test gets a small in-memory GeoIP table instead of the
GeoLite2 databases. The DNS leak test is off unless a test asks for the
`resolver_store` fixture.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from geoip2.errors import AddressNotFoundError

from ip import dnsleak, main

DOMAIN = "example.test"


class FakeGeoIP:
    """
    Stands in for an open geoip2 Reader of either database. `records` maps an
    IP address to its location, and `errors` maps an IP address to an
    exception that its lookup raises.
    """

    def __init__(self):
        self.records: dict[str, SimpleNamespace] = {}
        self.errors: dict[str, Exception] = {}

    def add(
        self,
        ip: str,
        org: str | None,
        country: str | None = None,
        iso_code: str | None = None,
        city: str | None = None,
    ) -> None:
        self.records[ip] = SimpleNamespace(
            org=org, country=country, iso_code=iso_code, city=city
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _find(self, ip: str) -> SimpleNamespace:
        if ip in self.errors:
            raise self.errors[ip]
        if ip not in self.records:
            raise AddressNotFoundError(f"{ip} is not in the database.")
        return self.records[ip]

    def asn(self, ip: str) -> SimpleNamespace:
        return SimpleNamespace(autonomous_system_organization=self._find(ip).org)

    def city(self, ip: str) -> SimpleNamespace:
        record = self._find(ip)
        return SimpleNamespace(
            country=SimpleNamespace(name=record.country, iso_code=record.iso_code),
            city=SimpleNamespace(name=record.city),
        )


@pytest.fixture(autouse=True)
def geoip(monkeypatch, tmp_path) -> FakeGeoIP:
    """
    Replaces both GeoLite2 databases with a FakeGeoIP holding a few addresses.
    The database files exist but are empty, since only the fake reads them.
    """
    fake = FakeGeoIP()
    fake.add("8.8.8.8", "Google LLC", "United States", "US")
    fake.add("8.8.4.4", "Google LLC", "United States", "US", "Westfield")
    fake.add("2001:4860:4860::8888", "Google LLC", "United States", "US")
    fake.add("1.1.1.1", "Cloudflare, Inc.", "Australia", "AU")

    for name in ("GEOIP_CITY_DB_PATH", "GEOIP_ASN_DB_PATH"):
        path = tmp_path / f"{name}.mmdb"
        path.touch()
        monkeypatch.setattr(main, name, str(path))
    monkeypatch.setattr(main, "Reader", lambda path: fake)
    return fake


@pytest.fixture(autouse=True)
def dns_leak_off(monkeypatch) -> None:
    """
    Turns the DNS leak test off, even if DOMAIN is set in the environment.
    """
    monkeypatch.setattr(main, "DOMAIN", "")
    monkeypatch.setattr(main, "DNS_ZONE", None)


@pytest.fixture
def resolver_store(monkeypatch) -> dnsleak.ResolverStore:
    """
    Turns the DNS leak test on for dns.example.test with an empty store. The
    DNS server, if started, listens on a free port.
    """
    store = dnsleak.ResolverStore()
    monkeypatch.setattr(main, "DOMAIN", DOMAIN)
    monkeypatch.setattr(main, "DNS_ZONE", f"dns.{DOMAIN}")
    monkeypatch.setattr(main, "DNS_PORT", 0)
    monkeypatch.setattr(main, "resolver_store", store)
    return store


@pytest.fixture
def client() -> TestClient:
    """
    A client for the app. Its lifespan doesn't run, so no DNS server starts.
    """
    return TestClient(main.app)

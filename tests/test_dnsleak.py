"""Tests for the DNS leak test's resolver store and DNS server."""

import asyncio
from types import SimpleNamespace

import dns.asyncquery
import dns.flags
import dns.message
import dns.name
import dns.opcode
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset
import pytest

import dnsleak

DOMAIN = "example.test"
TEST_ID = "0123456789abcdef0123456789abcdef"
OTHER_ID = "fedcba9876543210fedcba9876543210"


@pytest.fixture
def clock(monkeypatch) -> SimpleNamespace:
    """A clock for the store that only moves when a test sets `now`."""
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(dnsleak, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.fixture
def store() -> dnsleak.ResolverStore:
    return dnsleak.ResolverStore()


@pytest.fixture
def zone(store) -> dnsleak.LeakTestZone:
    return dnsleak.LeakTestZone(DOMAIN, store)


def send(
    zone: dnsleak.LeakTestZone, query: dns.message.Message, client: str = "192.0.2.53"
) -> dns.message.Message:
    """Sends a query to the zone, as `client`, and parses its response."""
    return dns.message.from_wire(zone.respond(query.to_wire(), client))


def ask(
    zone: dnsleak.LeakTestZone,
    name: str,
    rdtype: str = "A",
    client: str = "192.0.2.53",
) -> dns.message.Message:
    return send(zone, dns.message.make_query(name, rdtype), client)


def test_store_keeps_resolvers_once_in_first_seen_order(store):
    for ip in ["192.0.2.2", "192.0.2.1", "192.0.2.2"]:
        store.add(TEST_ID, ip)
    assert store.get(TEST_ID) == ["192.0.2.2", "192.0.2.1"]
    assert store.get(OTHER_ID) == []


def test_store_caps_resolvers_per_test():
    store = dnsleak.ResolverStore(max_resolvers=2)
    for ip in ["192.0.2.1", "192.0.2.2", "192.0.2.3"]:
        store.add(TEST_ID, ip)
    assert store.get(TEST_ID) == ["192.0.2.1", "192.0.2.2"]


def test_store_caps_number_of_tests():
    store = dnsleak.ResolverStore(max_tests=1)
    store.add(TEST_ID, "192.0.2.1")
    store.add(OTHER_ID, "192.0.2.2")
    assert store.get(TEST_ID) == ["192.0.2.1"]
    assert store.get(OTHER_ID) == []


def test_store_forgets_test_after_ttl(clock):
    store = dnsleak.ResolverStore(ttl=60)
    store.add(TEST_ID, "192.0.2.1")
    clock.now += 59
    store.add(TEST_ID, "192.0.2.2")
    assert store.get(TEST_ID) == ["192.0.2.1", "192.0.2.2"]
    clock.now += 1
    assert store.get(TEST_ID) == []


def test_store_makes_room_once_tests_expire(clock):
    store = dnsleak.ResolverStore(ttl=60, max_tests=1)
    store.add(TEST_ID, "192.0.2.1")
    clock.now += 60
    store.add(OTHER_ID, "192.0.2.2")
    assert store.get(OTHER_ID) == ["192.0.2.2"]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.0.2.1", "192.0.2.1"),
        ("::ffff:192.0.2.1", "192.0.2.1"),
        ("2001:db8:0::1", "2001:db8::1"),
    ],
)
def test_normalise_ip(host, expected):
    assert dnsleak.normalise_ip(host) == expected


def test_lookup_in_zone_records_resolver(zone, store):
    response = ask(zone, f"{TEST_ID}-0.dns.{DOMAIN}")
    assert response.rcode() == dns.rcode.NXDOMAIN
    assert response.flags & dns.flags.AA
    assert response.answer == []
    assert response.authority == [zone.soa]
    assert store.get(TEST_ID) == ["192.0.2.53"]


def test_lookup_with_randomised_case_records_resolver(zone, store):
    ask(zone, f"{TEST_ID.upper()}-1.DNS.{DOMAIN}")
    assert store.get(TEST_ID) == ["192.0.2.53"]


def test_lookup_from_ipv4_mapped_address_records_ipv4(zone, store):
    ask(zone, f"{TEST_ID}-0.dns.{DOMAIN}", client="::ffff:192.0.2.53")
    assert store.get(TEST_ID) == ["192.0.2.53"]


@pytest.mark.parametrize(
    "label",
    [
        "www",
        TEST_ID,
        f"{TEST_ID}-123",
        f"{TEST_ID[:-1]}-0",
        f"{TEST_ID}-0.extra",
    ],
)
def test_lookup_of_other_names_records_nothing(zone, store, label):
    response = ask(zone, f"{label}.dns.{DOMAIN}")
    assert response.rcode() == dns.rcode.NXDOMAIN
    assert store._tests == {}


def test_zone_apex_answers_soa(zone):
    response = ask(zone, f"dns.{DOMAIN}", "SOA")
    assert response.rcode() == dns.rcode.NOERROR
    assert response.flags & dns.flags.AA
    assert response.answer == [zone.soa]
    # Resolvers cache NXDOMAIN for the SOA minimum, the store's TTL
    assert zone.soa[0].minimum == 60


def test_zone_apex_answers_ns(zone):
    response = ask(zone, f"dns.{DOMAIN}", "NS")
    assert response.answer == [zone.ns]
    assert zone.ns[0].target == dns.name.from_text(f"ns.{DOMAIN}")


def test_zone_apex_has_no_other_records(zone):
    response = ask(zone, f"dns.{DOMAIN}", "A")
    assert response.rcode() == dns.rcode.NOERROR
    assert response.answer == []
    assert response.authority == [zone.soa]


@pytest.mark.parametrize("name", [DOMAIN, f"xdns.{DOMAIN}", "dns.example.org"])
def test_lookup_outside_zone_is_refused(zone, store, name):
    response = ask(zone, name)
    assert response.rcode() == dns.rcode.REFUSED
    assert not response.flags & dns.flags.AA
    assert store._tests == {}


def test_lookup_in_other_class_is_refused(zone):
    query = dns.message.make_query(
        f"{TEST_ID}-0.dns.{DOMAIN}", "TXT", rdclass=dns.rdataclass.CH
    )
    response = send(zone, query)
    assert response.rcode() == dns.rcode.REFUSED


def test_zone_accepts_domain_with_trailing_dot(store):
    zone = dnsleak.LeakTestZone(f"{DOMAIN}.", store)
    ask(zone, f"{TEST_ID}-0.dns.{DOMAIN}")
    assert store.get(TEST_ID) == ["192.0.2.53"]


def test_other_opcodes_are_not_implemented(zone):
    query = dns.message.make_query(f"dns.{DOMAIN}", "SOA")
    query.set_opcode(dns.opcode.NOTIFY)
    response = send(zone, query)
    assert response.rcode() == dns.rcode.NOTIMP


def test_query_with_two_questions_is_malformed(zone):
    query = dns.message.make_query(f"{TEST_ID}-0.dns.{DOMAIN}", "A")
    query.question.append(
        dns.rrset.RRset(
            dns.name.from_text(f"{TEST_ID}-1.dns.{DOMAIN}"),
            dns.rdataclass.IN,
            dns.rdatatype.A,
        )
    )
    response = send(zone, query)
    assert response.rcode() == dns.rcode.FORMERR


def test_responses_and_garbage_are_dropped(zone):
    query = dns.message.make_query(f"{TEST_ID}-0.dns.{DOMAIN}", "A")
    response = dns.message.make_response(query)
    assert zone.respond(response.to_wire(), "192.0.2.53") is None
    assert zone.respond(b"\x00\x01", "192.0.2.53") is None


def test_serve_answers_over_udp_and_tcp(zone, store):
    async def look_up_over_both():
        async with dnsleak.serve(zone, 0) as port:
            udp = await dns.asyncquery.udp(
                dns.message.make_query(f"{TEST_ID}-0.dns.{DOMAIN}", "A"),
                "127.0.0.1",
                port=port,
                timeout=2,
            )
            tcp = await dns.asyncquery.tcp(
                dns.message.make_query(f"{TEST_ID}-1.dns.{DOMAIN}", "A"),
                "::1",
                port=port,
                timeout=2,
            )
        return udp, tcp

    udp, tcp = asyncio.run(look_up_over_both())
    assert udp.rcode() == dns.rcode.NXDOMAIN
    assert tcp.rcode() == dns.rcode.NXDOMAIN
    # IPv4 queries reach the dual-stack UDP socket as IPv4-mapped addresses
    assert store.get(TEST_ID) == ["127.0.0.1", "::1"]

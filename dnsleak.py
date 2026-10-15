"""
Authoritative DNS server for the DNS leak test.

The parent zone delegates dns.<DOMAIN> to this server. The home page makes the
browser look up random names under it, so whichever resolvers do the lookups
on the visitor's behalf have to ask this server, which records their addresses
under the test id in the name. The addresses are kept in memory only, and
expire shortly after the test.
"""

import asyncio
import contextlib
import ipaddress
import re
import struct
import time

import dns.flags
import dns.message
import dns.name
import dns.opcode
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset

# The page looks up <test id>-<n>.dns.<DOMAIN>, the id being 32 random hex digits
TEST_ID_PATTERN = r"^[0-9a-f]{32}$"
LABEL_PATTERN = re.compile(r"^([0-9a-f]{32})-\d{1,2}$")

# How long an idle TCP connection is kept open
TCP_TIMEOUT = 10


class ResolverStore:
    """
    Resolver addresses seen per test id, in the order they were first seen.
    A test expires `ttl` seconds after its first lookup, and the store's size
    is capped, so a flood of queries can't exhaust memory.
    """

    def __init__(
        self, ttl: float = 60, max_tests: int = 10_000, max_resolvers: int = 16
    ):
        self.ttl = ttl
        self.max_tests = max_tests
        self.max_resolvers = max_resolvers
        # Insertion order is creation order, so the oldest test comes first
        self._tests: dict[str, tuple[float, dict[str, None]]] = {}

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        while self._tests:
            test_id, (created, _) = next(iter(self._tests.items()))
            if created > cutoff:
                break
            del self._tests[test_id]

    def add(self, test_id: str, ip: str) -> None:
        self._expire()
        if test_id not in self._tests:
            if len(self._tests) >= self.max_tests:
                return
            self._tests[test_id] = (time.monotonic(), {})
        resolvers = self._tests[test_id][1]
        if len(resolvers) < self.max_resolvers:
            resolvers[ip] = None

    def get(self, test_id: str) -> list[str]:
        self._expire()
        entry = self._tests.get(test_id)
        return list(entry[1]) if entry else []


def normalise_ip(host: str) -> str:
    """
    Unwraps IPv4-mapped IPv6 addresses (::ffff:1.2.3.4), which a dual-stack
    socket reports for IPv4 clients.
    """
    address = ipaddress.ip_address(host)
    if address.version == 6 and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


class LeakTestZone:
    """
    Answers queries for dns.<domain>: SOA and NS at the apex, NXDOMAIN for
    every name below it (recording the resolver that asked), and REFUSED for
    anything outside the zone.
    """

    def __init__(self, domain: str, store: ResolverStore):
        domain = domain.rstrip(".")
        self.zone = dns.name.from_text(f"dns.{domain}")
        self.store = store
        nameserver = f"ns.{domain}."
        # The SOA minimum (last field) is how long resolvers cache NXDOMAIN
        self.soa = dns.rrset.from_text(
            self.zone,
            60,
            "IN",
            "SOA",
            f"{nameserver} hostmaster.{domain}. 1 3600 600 86400 60",
        )
        self.ns = dns.rrset.from_text(self.zone, 3600, "IN", "NS", nameserver)

    def respond(self, wire: bytes, client: str) -> bytes | None:
        """
        Builds the response to a query in wire format, or returns None if the
        packet should be dropped.
        """
        try:
            query = dns.message.from_wire(wire)
        except Exception:  # noqa: BLE001 - malformed packets are dropped
            return None

        # Never answer responses, so two servers can't bounce packets forever
        if query.flags & dns.flags.QR:
            return None

        response = dns.message.make_response(query)
        if query.opcode() != dns.opcode.QUERY:
            response.set_rcode(dns.rcode.NOTIMP)
            return response.to_wire()
        if len(query.question) != 1:
            response.set_rcode(dns.rcode.FORMERR)
            return response.to_wire()

        question = query.question[0]
        if question.rdclass != dns.rdataclass.IN or not question.name.is_subdomain(
            self.zone
        ):
            response.set_rcode(dns.rcode.REFUSED)
            return response.to_wire()

        response.flags |= dns.flags.AA
        if question.name == self.zone:
            if question.rdtype == dns.rdatatype.SOA:
                response.answer.append(self.soa)
            elif question.rdtype == dns.rdatatype.NS:
                response.answer.append(self.ns)
            else:
                response.authority.append(self.soa)
        else:
            self._record(question.name, client)
            response.set_rcode(dns.rcode.NXDOMAIN)
            response.authority.append(self.soa)
        return response.to_wire()

    def _record(self, name: dns.name.Name, client: str) -> None:
        labels = name.relativize(self.zone).labels
        if len(labels) != 1:
            return
        # Resolvers may randomise the case of the name (DNS 0x20), so lower it
        match = LABEL_PATTERN.match(labels[0].decode("ascii", "replace").lower())
        if match:
            self.store.add(match.group(1), normalise_ip(client))

    async def handle_tcp(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Serves one TCP connection: each message is prefixed with its length.
        """
        client = writer.get_extra_info("peername")[0]
        try:
            while True:
                async with asyncio.timeout(TCP_TIMEOUT):
                    (length,) = struct.unpack("!H", await reader.readexactly(2))
                    wire = await reader.readexactly(length)
                reply = self.respond(wire, client)
                if reply is None:
                    break
                writer.write(struct.pack("!H", len(reply)) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()


class _UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, zone: LeakTestZone):
        self.zone = zone

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        reply = self.zone.respond(data, addr[0])
        if reply is not None:
            self.transport.sendto(reply, addr)


@contextlib.asynccontextmanager
async def serve(zone: LeakTestZone, port: int):
    """
    Serves the zone over UDP and TCP on all addresses while the context is open.
    """
    loop = asyncio.get_running_loop()
    # "::" is dual-stack, so it receives IPv4 queries as IPv4-mapped addresses
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _UDPProtocol(zone), local_addr=("::", port)
    )
    server = await asyncio.start_server(zone.handle_tcp, port=port)
    try:
        yield
    finally:
        transport.close()
        server.close()

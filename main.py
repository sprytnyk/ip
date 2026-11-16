import ipaddress
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from geoip2.database import Reader
from geoip2.errors import AddressNotFoundError
from pydantic import BaseModel

import dnsleak

GEOIP_CITY_DB_PATH = "./city.mmdb"
GEOIP_ASN_DB_PATH = "./asn.mmdb"

# The DNS leak test serves dns.<DOMAIN>; without DOMAIN it's turned off.
DOMAIN = os.environ.get("DOMAIN", "").strip().rstrip(".")
DNS_ZONE = f"dns.{DOMAIN}" if DOMAIN else None
DNS_PORT = int(os.environ.get("DNS_PORT", "5300"))

# Resolvers seen by the DNS server, kept in this process's memory. That's
# why uvicorn must run a single worker: the API reads what the DNS server saw.
resolver_store = dnsleak.ResolverStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs the DNS server for the leak test alongside the API, if it's enabled.
    """
    if DOMAIN:
        zone = dnsleak.LeakTestZone(DOMAIN, resolver_store)
        async with dnsleak.serve(zone, DNS_PORT):
            yield
    else:
        yield


app = FastAPI(
    title="IP Location API",
    description=(
        "This API provides the geographical location of an IP address. "
        "It returns the country, city, and IP address information for both "
        "the client's IP or a specific IPv4 or IPv6 address."
    ),
    docs_url="/docs/",
    redoc_url=None,
    lifespan=lifespan,
)

# The home page fetches /api/address/ from the ipv4. and ipv6. subdomains,
# which are separate origins. The API is public and uses no cookies.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


class ClientLocationResponse(BaseModel):
    country: str
    iso_code: str
    city: str
    ip: str
    version: int
    org: str


class ClientAddressResponse(BaseModel):
    ip: str
    version: int


class DnsResolverResponse(ClientLocationResponse):
    same_network: bool | None


class DnsLeakResponse(BaseModel):
    leak: bool | None
    resolvers: list[DnsResolverResponse]


class Message(BaseModel):
    detail: str


# Response examples and error descriptions for the API docs
LOCATION_EXAMPLE = {
    "country": "Ukraine",
    "iso_code": "UA",
    "city": "Kropyvnytskyi",
    "ip": "192.168.1.1",
    "version": 4,
    "org": "Example ASN Organisation",
}

SERVER_ERROR_RESPONSE = {
    "model": Message,
    "description": "An internal server error occurred while processing the request",
}

LOCATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": Message,
        "description": "The given string is not a valid IP address",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": Message,
        "description": "IP address not found in the GeoIP database",
    },
    status.HTTP_500_INTERNAL_SERVER_ERROR: SERVER_ERROR_RESPONSE,
    status.HTTP_200_OK: {
        "description": "Location details retrieved successfully",
        "content": {"application/json": {"example": LOCATION_EXAMPLE}},
    },
}


def get_client_ip(request: Request) -> str:
    """
    Extracts the client's IP address from headers or request object.
    Checks 'X-Forwarded-For' and 'X-Real-IP' headers for the public IP.
    Falls back to the proxy IP if those headers are not found.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # The first address in the list is the client's
        return x_forwarded_for.split(",")[0].strip()
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip
    return request.client.host


def parse_ip(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """
    Parses an IPv4 or IPv6 address.
    IPv4-mapped IPv6 addresses (::ffff:1.2.3.4), which dual-stack sockets
    report for IPv4 clients, are unwrapped to plain IPv4.

    Raises:
        HTTPException: If the string is not a valid IP address.
    """
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ip} is not a valid IP address.",
        ) from None

    if address.version == 6 and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def lookup_ip(ip: str) -> ClientLocationResponse:
    """
    Looks up geographic and ASN data for the provided IP address.
    Retrieves data from the GeoIP City and ASN databases.

    Args:
        ip (str): The IP address to lookup.

    Returns:
        ClientLocationResponse: Contains country, city, organisation, and IP.

    Raises:
        HTTPException: If the IP is invalid, not found or an internal error occurs.
    """
    address = parse_ip(ip)
    ip = str(address)

    if not os.path.exists(GEOIP_ASN_DB_PATH) or not os.path.exists(GEOIP_CITY_DB_PATH):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GeoIP database not found.",
        )

    try:
        with Reader(GEOIP_ASN_DB_PATH) as asn_db:
            asn = asn_db.asn(ip)
        with Reader(GEOIP_CITY_DB_PATH) as city_db:
            location = city_db.city(ip)
    except AddressNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IP address {ip} not found in GeoIP database.",
        ) from None
    except Exception as e:
        # Any other failure becomes a 500, logged without the IP address
        print(f"Error looking up IP: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        ) from e

    return ClientLocationResponse(
        country=location.country.name or "Unknown",
        iso_code=location.country.iso_code or "Unknown",
        city=location.city.name or "Unknown",
        ip=ip,
        version=address.version,
        org=asn.autonomous_system_organization or "Unknown",
    )


def lookup_ip_or_none(ip: str) -> ClientLocationResponse | None:
    """
    Like lookup_ip, but returns None for invalid addresses and addresses that
    aren't in the GeoIP database instead of raising.
    """
    try:
        return lookup_ip(ip)
    except HTTPException as e:
        if e.status_code not in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ):
            raise
        return None


def same_network(
    resolver: ClientLocationResponse, client: ClientLocationResponse | None
) -> bool | None:
    """
    Compares the ASN organisations of a resolver and the client. Returns None
    if either is unknown, since then there's nothing to compare.
    """
    if client is None:
        return None
    if resolver.ip == client.ip:
        return True
    if "Unknown" in (resolver.org, client.org):
        return None
    return resolver.org == client.org


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Serves the favicon.ico file. Returns a temporary redirect.
    """
    return RedirectResponse(
        url="/static/favicon.ico", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def display_client_location(request: Request):
    """
    Renders an HTML page displaying the client's geographical location.
    If the lookup fails, renders an error page with the lookup's status code.
    """
    try:
        location = lookup_ip(get_client_ip(request))
    except HTTPException as e:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"detail": e.detail, "status_code": e.status_code},
            status_code=e.status_code,
        )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"data": location.model_dump(), "dns_zone": DNS_ZONE},
    )


@app.get(
    "/api/address/",
    response_model=ClientAddressResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Address retrieved successfully",
            "content": {
                "application/json": {"example": {"ip": "2001:db8::1", "version": 6}}
            },
        },
    },
)
async def get_requester_address(request: Request) -> ClientAddressResponse:
    """
    Returns the requester's IP address and whether it is IPv4 or IPv6,
    without a GeoIP lookup. The home page calls it on the ipv4. and ipv6.
    subdomains to find the client's address on the other protocol.
    """
    address = parse_ip(get_client_ip(request))
    return ClientAddressResponse(ip=str(address), version=address.version)


@app.get(
    "/api/ip/", response_model=ClientLocationResponse, responses=LOCATION_RESPONSES
)
async def get_requester_ip(request: Request) -> ClientLocationResponse:
    """
    Fetches the geographical location details of the client making the request.
    Returns the country, city, and organisation along with the client's IP address.
    If GeoIP database or IP lookup fails, a 404 or 500 error will be raised.
    """
    return lookup_ip(get_client_ip(request))


@app.get(
    "/api/ip/{ip}/",
    response_model=ClientLocationResponse,
    responses=LOCATION_RESPONSES,
)
async def get_ip_location(ip: str) -> ClientLocationResponse:
    """
    Fetches geographical and ASN details for a specific IP address.
    If IP is not found in the GeoIP database, a 404 error is raised.
    If an internal error occurs, a 500 error is raised.
    """
    return lookup_ip(ip)


@app.get(
    "/api/dns/{test_id}/",
    response_model=DnsLeakResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": Message,
            "description": "The DNS leak test isn't enabled on this server",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: SERVER_ERROR_RESPONSE,
        status.HTTP_200_OK: {
            "description": "Resolvers retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "leak": False,
                        "resolvers": [
                            {
                                **LOCATION_EXAMPLE,
                                "ip": "192.0.2.53",
                                "same_network": True,
                            }
                        ],
                    }
                }
            },
        },
    },
)
async def get_dns_resolvers(
    request: Request,
    test_id: Annotated[str, Path(pattern=dnsleak.TEST_ID_PATTERN)],
) -> DnsLeakResponse:
    """
    Returns the DNS resolvers that looked up <test_id>-<n>.dns.<domain> in the
    last minute, with their location details. The home page runs the lookups
    and then calls this.

    Each resolver's `same_network` says whether it belongs to the same network
    (ASN organisation) as the requester's IP address, and `leak` is true if any
    resolver doesn't. Behind a VPN, that means DNS queries bypass the tunnel,
    unless the resolver is a DNS provider you chose. Either is null when it
    can't be told, e.g. when an address isn't in the GeoIP database.
    """
    if not DNS_ZONE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The DNS leak test isn't enabled on this server.",
        )

    client = lookup_ip_or_none(get_client_ip(request))
    resolvers = []
    for ip in resolver_store.get(test_id):
        location = lookup_ip_or_none(ip) or ClientLocationResponse(
            country="Unknown",
            iso_code="Unknown",
            city="Unknown",
            ip=ip,
            version=ipaddress.ip_address(ip).version,
            org="Unknown",
        )
        resolvers.append(
            DnsResolverResponse(
                **location.model_dump(), same_network=same_network(location, client)
            )
        )

    verdicts = {resolver.same_network for resolver in resolvers}
    if False in verdicts:
        leak = True
    elif verdicts == {True}:
        leak = False
    else:
        leak = None
    return DnsLeakResponse(leak=leak, resolvers=resolvers)

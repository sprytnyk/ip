import os
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from geoip2.database import Reader
from geoip2.errors import AddressNotFoundError
from pydantic import BaseModel

# Paths to GeoIP databases
GEOIP_CITY_DB_PATH = "./city.mmdb"
GEOIP_ASN_DB_PATH = "./asn.mmdb"

app = FastAPI(
    title="IP Location API",
    description=(
        "This API provides the geographical location of an IP address. "
        "It returns the country, city, and IP address information for both "
        "the client's IP or a specific IP address."
    ),
    docs_url="/docs/",
    redoc_url="/redoc/",
)

# Mount the static directory to serve static files (like favicon)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 template directory
templates = Jinja2Templates(directory="templates")


# Define the response model for the client's location
class ClientLocationResponse(BaseModel):
    country: str
    iso_code: str
    city: str
    ip: str
    org: str


# Define the error message model
class Message(BaseModel):
    detail: str


# Function to retrieve the client's IP address from the request headers
def get_client_ip(request: Request) -> str:
    """
    Extracts the client's IP address from headers or request object.
    Checks 'X-Forwarded-For' and 'X-Real-IP' headers for the public IP.
    Falls back to the proxy IP if those headers are not found.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    x_real_ip = request.headers.get("X-Real-IP")

    if x_forwarded_for:
        # Return the first IP in the list (client's real IP)
        return x_forwarded_for.split(",")[0].strip()
    elif x_real_ip:
        return x_real_ip
    else:
        # Fallback to request.client.host (proxy IP)
        return request.client.host


# Function to perform the IP lookup and return location details
def lookup_ip(ip: str) -> ClientLocationResponse:
    """
    Looks up geographic and ASN data for the provided IP address.
    Retrieves data from the GeoIP City and ASN databases.

    Args:
        ip (str): The IP address to lookup.

    Returns:
        ClientLocationResponse: Contains country, city, organisation, and IP.

    Raises:
        HTTPException: If the IP is not found or an internal error occurs.
    """
    # Ensure that the GeoIP ASN database exists
    if not os.path.exists(GEOIP_ASN_DB_PATH) or not os.path.exists(GEOIP_CITY_DB_PATH):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GeoIP database not found.",
        )

    try:
        # Open the ASN database and retrieve ASN data
        with Reader(GEOIP_ASN_DB_PATH) as asn_db:
            asn = asn_db.asn(ip)

        # Open the City database and retrieve location data
        with Reader(GEOIP_CITY_DB_PATH) as city_db:
            metadata = city_db.city(ip)

        # Return the combined location and ASN data
        return ClientLocationResponse(
            country=metadata.country.name or "Unknown",
            iso_code=metadata.country.iso_code or "Unknown",
            city=metadata.city.name or "Unknown",
            ip=ip,
            org=asn.autonomous_system_organization or "Unknown",
        )

    except AddressNotFoundError:
        # Raise a 404 if the IP is not found in the database
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IP address {ip} not found in GeoIP database.",
        )

    except Exception as e:
        # Catch any unexpected errors and log them
        print(f"Error looking up IP {ip}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


# Route to serve the favicon.ico
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Serves the favicon.ico file. Returns a temporary redirect.
    """
    return RedirectResponse(
        url="/static/favicon.ico", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


# Route to render an HTML page with location details for the client
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def display_client_location(request: Request):
    """
    Renders an HTML page displaying the client's geographical location.
    If the GeoIP database is not found, returns a status 500 error.
    If the IP lookup fails, a 404 error is returned.
    """
    try:
        # Get the client's IP address (hardcoded example for testing)
        client_ip = get_client_ip(request)
        # Perform the lookup using the provided IP address
        location_data = lookup_ip(client_ip)
        # Render the HTML template with the location data
        return templates.TemplateResponse(
            "index.html", {"request": request, "data": location_data.dict()}
        )
    except HTTPException as e:
        # If an error occurs, render the error template with the error details
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "detail": e.detail, "status_code": e.status_code},
            status_code=e.status_code,
        )


# Route to get location details for the requester's IP address
@app.get(
    "/api/ip/",
    response_model=ClientLocationResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": Message,
            "description": "IP address not found in the GeoIP database",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": Message,
            "description": "An internal server error occurred while processing the request",
        },
        status.HTTP_200_OK: {
            "description": "Location details retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "country": "Ukraine",
                        "iso_code": "UA",
                        "city": "Kropyvnytskyi",
                        "ip": "192.168.1.1",
                        "org": "Example ASN Organisation",
                    }
                }
            },
        },
    },
)
async def get_requester_ip(request: Request) -> ClientLocationResponse:
    """
    Fetches the geographical location details of the client making the request.
    Returns the country, city, and organisation along with the client's IP address.
    If GeoIP database or IP lookup fails, a 404 or 500 error will be raised.
    """
    # Retrieve the client's IP address
    client_ip = get_client_ip(request)
    return lookup_ip(client_ip)


# Route to get location details for a specific IP address
@app.get(
    "/api/ip/{ip}/",
    response_model=ClientLocationResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": Message,
            "description": "IP address not found in the GeoIP database",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": Message,
            "description": "Internal server error occurred while processing the request",
        },
        status.HTTP_200_OK: {
            "description": "Location details retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "country": "Ukraine",
                        "iso_code": "UA",
                        "city": "Kropyvnytskyi",
                        "ip": "192.168.1.1",
                        "org": "Example ASN Organisation",
                    }
                }
            },
        },
    },
)
async def get_ip_location(ip: str) -> ClientLocationResponse:
    """
    Fetches geographical and ASN details for a specific IP address.
    If IP is not found in the GeoIP database, a 404 error is raised.
    If an internal error occurs, a 500 error is raised.
    """
    return lookup_ip(ip)

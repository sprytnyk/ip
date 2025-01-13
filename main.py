import os

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from geoip2.database import Reader
from geoip2.errors import AddressNotFoundError
from pydantic import BaseModel

app = FastAPI(
    docs_url="/docs/",
    redoc_url="/redoc/"
)

# Path to GeoIP database
GEOIP_DB_PATH = "./db.mmdb"

# Mount the static directory to serve the favicon
app.mount("/static", StaticFiles(directory="static"), name="static")


# Route to serve the favicon.ico
@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse(url="/static/favicon.ico")


# Define the ClientLocationResponse model
class ClientLocationResponse(BaseModel):
    country: str
    city: str
    ip: str


# Function to get client IP address
def get_client_ip(request: Request) -> str:
    """
    Extracts the client's IP address from headers or the request object.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    x_real_ip = request.headers.get("X-Real-IP")
    if x_forwarded_for:
        # The first IP in the list is the public client IP
        return x_forwarded_for.split(",")[0].strip()
    elif x_real_ip:
        return x_real_ip
    else:
        # Fallback to request.client.host (proxy IP)
        return request.client.host


# Function to look up geographic data for an IP address
def lookup_ip(ip: str) -> ClientLocationResponse:
    """
    Looks up geographic data for the given IP address using the GeoIP database.
    """
    # Verify the GeoIP database file exists
    if not os.path.exists(GEOIP_DB_PATH):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GeoIP database not found.",
        )

    try:
        # Open the GeoIP database and fetch location data
        with Reader(GEOIP_DB_PATH) as reader:
            metadata = reader.city(ip)
            return ClientLocationResponse(
                country=metadata.country.name or "Unknown",
                city=metadata.city.name or "Unknown",
                ip=ip,
            )
    except AddressNotFoundError:
        # Handle cases where the IP is not found in the database
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IP address {ip} not found in GeoIP database.",
        )
    except Exception as e:
        # Log the error and return a generic server error
        print(f"Error looking up IP {ip}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


# Route to get the requester's location details
@app.get("/", response_model=ClientLocationResponse, status_code=status.HTTP_200_OK)
async def get_requester_ip(request: Request) -> ClientLocationResponse:
    """
    Get the location details of the client making the request.
    - Returns the country, city, and IP address of the client.
    - If GeoIP database not found, returns status 500.
    - If IP address lookup fails, returns status 404.
    """
    # Get the client's IP address
    client_ip = get_client_ip(request)
    return lookup_ip(client_ip)


# Route to get location details for a specific IP address
@app.get(
    "/{ip}/", response_model=ClientLocationResponse, status_code=status.HTTP_200_OK
)
async def get_ip_location(ip: str) -> ClientLocationResponse:
    """
    Get the location details for a specific IP address.
    - Returns the country, city, and IP address for the given IP.
    - If the IP is not found in the GeoIP database, returns status 404.
    - If GeoIP database not found, returns status 500.
    """
    return lookup_ip(ip)

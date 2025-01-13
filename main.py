import httpx
from fastapi import FastAPI
from geoip2.database import Reader
from pydantic import BaseModel

app = FastAPI()


class HomeResponse(BaseModel):
    country: str
    city: str
    ip: str


@app.get("/")
def home() -> HomeResponse:
    data = {}
    with Reader("./db.mmdb") as reader:
        response = httpx.get("https://ipinfo.io/ip")
        ip = response.text
        metadata = reader.city(ip)
        data["country"] = metadata.country.name
        data["city"] = metadata.city.name
        data["ip"] = ip

    return data

# ip

A small web page and JSON API that shows your public IPv4 and IPv6 addresses,
with the country, city and network (ASN) they belong to. Built with FastAPI,
served behind Caddy, and looked up locally in the MaxMind GeoLite2 databases.

## Privacy

Your data isn't collected:

- Lookups happen against local GeoLite2 files. No address is sent to a
  third-party service.
- Nothing is written to a database or to disk; the API container runs with a
  read-only filesystem.
- The API runs uvicorn with `--no-access-log`, so requests aren't logged, and
  error messages don't include the IP address.
- Caddy has no `log` directive, so it doesn't write access logs either.
- The home page uses no cookies, analytics or external scripts.

The interactive API docs at `/docs/` and `/redoc/` are FastAPI's defaults,
which load Swagger UI and ReDoc from `cdn.jsdelivr.net` (and ReDoc also loads
Google Fonts), so opening those pages contacts those services.

## API

| Endpoint             | Returns                                                         |
| -------------------- | --------------------------------------------------------------- |
| `GET /`              | HTML page with your address and location                        |
| `GET /api/address/`  | Your IP address and its version, without a GeoIP lookup         |
| `GET /api/ip/`       | Location details for your IP address                            |
| `GET /api/ip/{ip}/`  | Location details for any IPv4 or IPv6 address                   |

```console
$ curl https://example.com/api/ip/192.0.2.10/
{"country":"...","iso_code":"...","city":"...","ip":"192.0.2.10","version":4,"org":"..."}
```

Errors return `{"detail": "..."}` with status 400 (not an IP address), 404 (not
in the database) or 500 (database missing or lookup failure). The OpenAPI docs
are at `/docs/` and `/redoc/`.

## How both addresses are detected

A request reaches the server over IPv4 or IPv6, not both. To show the other
address, the page fetches `/api/address/` from two subdomains:

- `ipv4.<domain>` has only an A record, so the browser must connect over IPv4.
- `ipv6.<domain>` has only an AAAA record, so the browser must connect over IPv6.

If a fetch fails, that protocol is shown as "Not detected".

## Deployment

1. Point DNS at the server: `<domain>` with A and AAAA records,
   `ipv4.<domain>` with only an A record, and `ipv6.<domain>` with only an
   AAAA record.
2. Download the GeoLite2 City and ASN databases from
   [MaxMind](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/)
   and save them as `city.mmdb` and `asn.mmdb` in the project root. They're
   mounted read-only into the container, so updating them on the host is
   enough.
3. Copy `.env.example` to `.env` and set `DOMAIN`.
4. Start the stack:

   ```console
   docker compose up -d --build
   ```

Caddy gets TLS certificates for all three hostnames automatically.

## Development

```console
uv venv
uv pip install -r requirements.txt
.venv/bin/fastapi dev main.py
```

Linting and formatting run through [pre-commit](https://pre-commit.com/)
(ruff and hadolint):

```console
pre-commit install
pre-commit run --all-files
```

## License

[MIT](LICENSE)

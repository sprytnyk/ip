.PHONY: all run run-dns check lint test format audit clean

all: check

run:
	uv run uvicorn ip.main:app --reload

# the app with the DNS leak test on; its DNS server listens on port 5300
run-dns:
	DOMAIN=example.test uv run uvicorn ip.main:app --reload

check: lint test

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

format:
	uv run ruff format .

# known vulnerabilities in the locked dependencies
audit:
	@requirements=$$(mktemp) && \
	uv export --quiet --locked --no-emit-project --format requirements-txt -o "$$requirements" && \
	uv run pip-audit --strict --require-hashes --disable-pip -r "$$requirements"; \
	status=$$?; rm -f "$$requirements"; exit $$status

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +

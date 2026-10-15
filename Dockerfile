FROM python:3.13-slim-bookworm

LABEL maintainer="Vladyslav Krylasov <vladyslav.krylasov@gmail.com>"

# Install security upgrades and clean up afterwards
RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN groupadd --system user && \
    useradd -g user --create-home --shell /bin/bash user

# Set environment variables
ENV HOME="/home/user"
ENV APP="${HOME}/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONOPTIMIZE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1

WORKDIR "${APP}"

# Copy only the necessary files for installation first (use caching)
COPY ./requirements.txt ./requirements.txt

RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy only what the app needs: no .mmdb files, compose files or VCS metadata.
# Files stay owned by root, so the app user can read but not modify them.
COPY main.py dnsleak.py ./
COPY templates ./templates
COPY static ./static

# Switch to the non-root user
USER user

# Expose the application port and the DNS leak test's nameserver
EXPOSE 8000 5300/udp 5300/tcp

# Default command. Run uvicorn directly: `fastapi run` has no option to turn
# off the access log, which would write every visitor's IP to the container output.
# Keep a single worker: the DNS leak test keeps its results in process memory.
CMD [ "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log" ]

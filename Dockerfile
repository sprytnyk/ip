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

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# Copy only what the app needs: no .mmdb files, compose files or VCS metadata.
# Files stay owned by root, so the app user can read but not modify them.
COPY main.py ./
COPY templates ./templates
COPY static ./static

# Switch to the non-root user
USER user

# Expose the default application port
EXPOSE 8000

# Default command
CMD [ "fastapi", "run", "./main.py", "--proxy-headers", "--port", "8000" ]

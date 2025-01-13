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
ENV PATH="${HOME}/.local/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONOPTIMIZE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1

# Create application directory with proper permissions
RUN mkdir -p "${APP}" && chown user:user "${APP}"
WORKDIR "${APP}"

# Copy only the necessary files for installation first (use caching)
COPY ./requirements.txt ./requirements.txt

# Upgrade pip and install dependencies as the non-root user
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r "${APP}/requirements.txt"

# Copy the remaining application code
COPY . .

# Ensure all files are owned by the non-root user
RUN chown -R user:user "${APP}"

# Switch to the non-root user
USER user

# Expose the default application port
EXPOSE 8000

# Default command
cMD [ "fastapi", "run", "./main.py", "--proxy-headers", "--port", "8000" ]

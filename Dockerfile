FROM python:3.13-slim-book

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
ENV HOME="/home/user" \
    APP="${HOME}/app" \
    PATH="${HOME}/.local/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1

# Create application directory with proper permissions
RUN mkdir -p "${APP}" && chown user:user "${APP}"
WORKDIR "${APP}"

# Copy only the necessary files for installation first (use caching)
COPY ./conf/ /conf/

# Upgrade pip and install dependencies as the non-root user
RUN python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir -r ./conf/requirements.txt

# Copy the remaining application code
COPY . .

# Ensure all files are owned by the non-root user
RUN chown -R user:user "${APP}"

# Switch to the non-root user
USER user

# Expose the default application port
EXPOSE 8000

# Default command
CMD ["fastapi", "run", "app/main.py", "--proxy-headers", "--port", "8000"]

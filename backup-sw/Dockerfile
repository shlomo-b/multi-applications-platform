# Use slim variant for smaller image size
FROM python:3.9-slim

# Set working directory
WORKDIR /app


# for CI github actions
COPY backup-sw/requirements.txt /app

# for local testing
# COPY requirements.txt .

RUN pip install --upgrade pip

# Install dependencies in a single RUN command to reduce layers
# Clean up apt cache to reduce image size
# Update packages first to get latest security fixes (including OpenSSL)
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        curl \
        iputils-ping \
        net-tools && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# for CI github actions
COPY backup-sw/juniper-sw.py /app

# for local testing
# COPY juniper-sw.py .

# ------------ NEW (security best practice) -------------
# Create a non-root user
RUN adduser --disabled-password --gecos "" appuser --uid 1000

# Make sure the user owns the files
RUN chown -R appuser:appuser /app

# Switch to the new user
USER appuser

# Start application when container is run
CMD ["python", "juniper-sw.py"]

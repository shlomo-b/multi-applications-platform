# Use slim variant for smaller image size
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# for CI github actions
COPY backup-juniper-sw/requirements.txt /usr/local/app/

# for local testing
# COPY requirements.txt /usr/local/app/


# Install dependencies in a single RUN command to reduce layers
# Clean up apt cache to reduce image size
# Update packages first to get latest security fixes (including OpenSSL)

# hadolint ignore=DL3008
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
      curl \
      iputils-ping \
      net-tools && \
    python -m pip install --no-cache-dir -r /usr/local/app/requirements.txt && \
    rm -rf /var/lib/apt/lists/*
# for CI github actions
COPY backup-juniper-sw/juniper-sw.py backup-juniper-sw/metrics.py backup-juniper-sw/cloud_upload.py backup-juniper-sw/cronjob.py backup-juniper-sw/connected-cron-ui/register.py /usr/local/app/

# for local testing
# COPY juniper-sw.py metrics.py cloud_upload.py cronjob.py connected-cron-ui/register.py /usr/local/app/

# ------------ NEW (security best practice) -------------
# Create a non-root user
RUN adduser --disabled-password --gecos "" appuser --uid 1000

# Make sure the user owns the files
RUN chown -R appuser:appuser /usr/local/app && \
    chown -R appuser:appuser /app

# Switch to the new user
USER appuser

# Start connector in the background, then the backup. Logs go to /app (writable in Docker and K8s).
CMD ["sh", "-c", "python /usr/local/app/register.py & python -u /usr/local/app/juniper-sw.py 2>&1 | tee /app/cronjob.log"]

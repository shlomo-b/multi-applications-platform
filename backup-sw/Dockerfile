# Use slim variant for smaller image size
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY backup-sw/requirements.txt /app

RUN pip install --upgrade pip

# Install dependencies in a single RUN command to reduce layers
# Clean up apt cache to reduce image size
RUN pip install --no-cache-dir -r requirements.txt && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl \
        iputils-ping \
        net-tools && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY backup-sw/juniper-sw.py /app


# Start application when container is run
CMD ["python", "juniper-sw.py"]

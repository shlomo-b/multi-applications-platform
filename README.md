# Multi-Applications Platform

A platform for automated backup of network devices (Firewalls and Switches) and a simple web-based Blackjack game, all with optional metrics collection.

## Overview

This platform consists of three main applications:

###  backup-fw (Fortigate Firewall Backup)
- Connects to Fortigate firewalls via SSH
- Retrieves full configuration using `show full-configuration` command
- Saves configuration to local file (`fortigate_backup.conf`)
- Optionally uploads to cloud storage (AWS S3, Azure Blob Storage)
- Optionally sends metrics to Prometheus Pushgateway

###  backup-sw (Juniper Switch Backup)
- Connects to Juniper switches via SSH
- Enters CLI mode and retrieves configuration using `show configuration | display set`
- Saves configuration to local file (`juniper_backup.txt`)
- Optionally uploads to cloud storage (AWS S3, Azure Blob Storage)
- Optionally sends metrics to Prometheus Pushgateway

###  blackjack-app (Blackjack Game)
- Simple web-based Blackjack card game
- Flask web server running on port 80
- Interactive HTML/JavaScript game interface
- Prometheus metrics exposed at `/metrics` endpoint
- Tracks games played, site visits, HTTP requests, and system metrics

## Features

- ✅ **SSH-based backup** - Secure connection to network devices
- ✅ **Cloud storage** - AWS S3 and Azure Blob Storage support (GCP coming soon)
- ✅ **Metrics collection** - Prometheus metrics via Pushgateway (backup apps) and `/metrics` endpoint (blackjack)
- ✅ **Modular architecture** - Separated concerns (metrics, cloud upload, main logic)
- ✅ **Local storage** - Backup files stored in container when cloud is disabled
- ✅ **Web-based game** - Simple Blackjack game with interactive UI

## Configuration

### Environment Variables

#### Required Variables

**For backup-fw:**
- `HOST` - Fortigate firewall IP address or hostname
- `PORT` - SSH port (default: 22)
- `USERNAME` - SSH username
- `PASSWORD` - SSH password
- `FW_NAME` - Firewall name/identifier (used to detect prompt)

**For backup-sw:**
- `HOST` - Juniper switch IP address or hostname
- `PORT` - SSH port (default: 22)
- `USERNAME` - SSH username
- `PASSWORD` - SSH password
- `SW_NAME` - Switch prompt identifier (e.g., `@Switch>`)

**For blackjack-app:**
- No environment variables required - runs on port 80 by default

#### Optional Features

**Cloud Storage (AWS S3):**
- `aws` - Enable AWS S3 upload (`true`/`false`, default: `false`)
- `AWS_ACCESS_KEY_ID` - AWS access key ID (required if `aws=true`)
- `AWS_SECRET_ACCESS_KEY` - AWS secret access key (required if `aws=true`)
- `BUCKET_NAME` - S3 bucket name (required if `aws=true`)

**Cloud Storage (Azure Blob Storage):**
- `azure` - Enable Azure upload (`true`/`false`, default: `false`)
- *Azure credentials and configuration (coming soon)*

**Cloud Storage (GCP Cloud Storage):**
- *GCP support in progress*

**Metrics (Prometheus Pushgateway):**
- `metrics-pushgw` - Enable metrics collection (`true`/`false`, default: `false`)
- `PUSHGATEWAY_ADDR` - Pushgateway address (default: `pushgateway:9091`)
- `PUSHGATEWAY_JOB` - Job name for metrics (default: `backup-fw` or `backup-sw`)
- `PUSHGATEWAY_INSTANCE` - Instance identifier (default: `HOST` or `unknown`)

## Usage

### Enable AWS S3 Upload

Set the following environment variables:
```bash
aws=true
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
BUCKET_NAME=your_bucket_name
```

When AWS is enabled:
- Backup files are uploaded to S3 with format: `backup-fw/fortigate_backup_YYYY-MM-DD_HHMMSS.conf` or `backup-sw/juniper_backup_YYYY-MM-DD_HHMMSS.txt`
- Local backup file is **deleted** after successful upload

### Enable Azure Blob Storage

Set the following environment variables:
```bash
azure=true
# Azure credentials (coming soon)
```

### Enable Metrics Collection

Set the following environment variables:
```bash
metrics-pushgw=true
PUSHGATEWAY_ADDR=your_pushgateway_address
PUSHGATEWAY_JOB=backup-fw
PUSHGATEWAY_INSTANCE=your_instance_name
```

### Local Storage Only (No Cloud Upload)

If both `aws=false` and `azure=false` (or not set):
- Backup file is saved locally in the container
- File path is logged: `📁 Path: /app/fortigate_backup.conf` or `/app/juniper_backup.txt`
- File remains in container until next backup or pod restart

## Metrics

### backup-fw Metrics

All metrics are prefixed with `backup_`:

#### Counters
- `backup_connection_success_total` - Total successful firewall connections
- `backup_connection_failure_total{error_type}` - Total failed connections
  - `error_type`: `authentication_error`, `ssh_error`, `connection_error`
- `backup_configuration_success_total` - Total successful configuration backups
- `backup_configuration_failure_total{error_type}` - Total failed backups
  - `error_type`: `configuration_error`
- `backup_s3_upload_success_total` - Total successful S3 uploads
- `backup_s3_upload_failure_total{error_type}` - Total failed S3 uploads
  - `error_type`: `file_not_found`, `missing_bucket_name`, `s3_client_error`, `upload_error`, `unknown_error`

#### Gauges
- `backup_s3_last_file_size_bytes` - Size of last uploaded file (bytes)
- `backup_s3_total_bytes_uploaded` - Total bytes uploaded (accumulated)
- `backup_last_success_timestamp{operation}` - Unix timestamp of last success
  - `operation`: `connection`, `configuration`, `s3_upload`
- `backup_last_failure_timestamp{operation}` - Unix timestamp of last failure
  - `operation`: `connection`, `configuration`, `s3_upload`

#### Histograms
- `backup_duration_seconds{operation}` - Duration of operations (seconds)
  - `operation`: `configuration`, `s3_upload`, `total`
  - Buckets: `[1, 5, 10, 30, 60, 120, 300, 600]`

### backup-sw Metrics

All metrics are prefixed with `backup_sw_`:

#### Counters
- `backup_sw_connection_success_total` - Total successful switch connections
- `backup_sw_connection_failure_total{error_type}` - Total failed connections
  - `error_type`: `authentication_error`, `ssh_error`, `connection_error`
- `backup_sw_configuration_success_total` - Total successful configuration backups
- `backup_sw_configuration_failure_total{error_type}` - Total failed backups
  - `error_type`: `configuration_error`
- `backup_sw_s3_upload_success_total` - Total successful S3 uploads
- `backup_sw_s3_upload_failure_total{error_type}` - Total failed S3 uploads
  - `error_type`: `file_not_found`, `missing_bucket_name`, `s3_client_error`, `upload_error`, `unknown_error`

#### Gauges
- `backup_sw_s3_last_file_size_bytes` - Size of last uploaded file (bytes)
- `backup_sw_s3_total_bytes_uploaded` - Total bytes uploaded (accumulated)
- `backup_sw_last_success_timestamp{operation}` - Unix timestamp of last success
  - `operation`: `connection`, `configuration`, `s3_upload`
- `backup_sw_last_failure_timestamp{operation}` - Unix timestamp of last failure
  - `operation`: `connection`, `configuration`, `s3_upload`

#### Histograms
- `backup_sw_duration_seconds{operation}` - Duration of operations (seconds)
  - `operation`: `configuration`, `s3_upload`, `total`
  - Buckets: `[1, 5, 10, 30, 60, 120, 300, 600]`

### blackjack-app Metrics

All metrics are exposed at `/metrics` endpoint:

#### Counters
- `games_played` - Total number of Blackjack games played
- `site_visits` - Total number of visits to the Blackjack site
- `http_requests_total{method, endpoint, status_code}` - Total HTTP requests
  - `method`: HTTP method (GET, POST, etc.)
  - `endpoint`: Request path (/, /metrics, /presentation, etc.)
  - `status_code`: HTTP status code (200, 404, etc.)

#### Gauges
- `cpu_usage_percent` - Current CPU usage percentage
- `memory_usage_bytes` - Current memory usage in bytes
- `network_io_bytes{direction}` - Network I/O counters
  - `direction`: `in` (bytes received) or `out` (bytes sent)

#### Histograms
- `http_request_duration_seconds{method, endpoint}` - HTTP request duration (seconds)
  - `method`: HTTP method (GET, POST, etc.)
  - `endpoint`: Request path (/, /metrics, /presentation, etc.)

## Docker Compose

Example `docker-compose.yml`:

```yaml
services:
  backup-fw:
    build:
      context: ./backup-fw
      dockerfile: Dockerfile
    environment:
      - HOST=your_host
      - PORT=22
      - USERNAME=your_username
      - PASSWORD=your_password
      - FW_NAME=your_fw_name
      - aws=true
      - azure=false
      - metrics-pushgw=true
      - AWS_ACCESS_KEY_ID=your_aws_access_key_id
      - AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
      - BUCKET_NAME=your_bucket_name
      - PUSHGATEWAY_ADDR=your_pushgateway_address
      - PUSHGATEWAY_JOB=backup-fw
      - PUSHGATEWAY_INSTANCE=your_instance_name
    volumes:
      - backup-fw-backups:/app
    restart: no

  backup-sw:
    build:
      context: ./backup-sw
      dockerfile: Dockerfile
    environment:
      - HOST=your_host
      - PORT=22
      - USERNAME=your_username
      - PASSWORD=your_password
      - SW_NAME=your_sw_name
      - aws=true
      - azure=false
      - metrics-pushgw=true
      - AWS_ACCESS_KEY_ID=your_aws_access_key_id
      - AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
      - BUCKET_NAME=your_bucket_name
      - PUSHGATEWAY_ADDR=your_pushgateway_address
      - PUSHGATEWAY_JOB=backup-sw
      - PUSHGATEWAY_INSTANCE=your_instance_name
    volumes:
      - backup-sw-backups:/app
    restart: no

  blackjack-app:
    build:
      context: .
      dockerfile: blackjack-app/Dockerfile
    ports:
      - "8080:80"
    restart: unless-stopped
```

## Architecture

The applications follow a modular architecture:

```
backup-fw/
├── fortigate_backup.py    # Main script (SSH connection, config retrieval)
├── cloud_upload.py        # Cloud storage logic (AWS/Azure)
├── metrics.py             # Prometheus metrics and Pushgateway push
└── Dockerfile

backup-sw/
├── juniper-sw.py          # Main script (SSH connection, config retrieval)
├── cloud_upload.py        # Cloud storage logic (AWS/Azure)
├── metrics.py             # Prometheus metrics and Pushgateway push
└── Dockerfile

blackjack-app/
├── main.py                # Flask web server and metrics
├── blackjack.html         # Game UI and JavaScript
├── presentation.html      # Presentation page (if exists)
└── Dockerfile
```

## Usage Examples

### Running Blackjack App

The blackjack app runs as a simple Flask web server:

```bash
# Build and run with Docker Compose
docker-compose up blackjack-app

# Or build manually
cd blackjack-app
docker build -t blackjack-app .
docker run -p 8080:80 blackjack-app
```

Access the game at `http://localhost:8080`:
- Main game: `http://localhost:8080/`
- Presentation page: `http://localhost:8080/presentation`
- Metrics endpoint: `http://localhost:8080/metrics`

The game features:
- Username input (minimum 2 characters)
- Interactive card game with hit/stand options
- Automatic dealer play
- Win/loss/draw detection

## Exit Codes

**Backup applications (backup-fw, backup-sw):**
- `0` - Success (configuration retrieved and cloud upload succeeded if enabled)
- `1` - Failure (connection error, configuration error, or cloud upload failure)

**Blackjack app:**
- Runs continuously as a web server (no exit codes)

## Notes

**Backup Applications:**
- When cloud upload is disabled, backup files are stored locally in the container
- When cloud upload is enabled and succeeds, local backup files are automatically deleted
- Metrics are only collected and pushed when `metrics-pushgw=true`
- All metrics support accumulation across multiple runs via Pushgateway
- S3 object names include date/time: `backup-fw/fortigate_backup_2026-02-07_123456.conf`

**Blackjack App:**
- Simple Flask web application - no configuration needed
- Metrics are always enabled and exposed at `/metrics` endpoint
- System metrics (CPU, memory, network) are updated on each `/metrics` request
- Game metrics increment automatically when users play or visit the site

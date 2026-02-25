"""Palo Alto backup: fetch running config via API and upload to cloud or keep locally."""
import os
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

import cloud_upload
import metrics

urllib3.disable_warnings(InsecureRequestWarning)

# Configuration from environment
HOST = os.environ.get('HOST')
PORT = os.environ.get('PORT', '443')
USERNAME = os.environ.get('USERNAME')
PASSWORD = os.environ.get('PASSWORD')
backup_file = "palo_alto_backup.xml"
VERIFY_SSL = os.environ.get('VERIFY_SSL', 'false').lower() == 'true'

USE_METRICS = os.environ.get('metrics-pushgw', 'false').lower() == 'true'
PUSHGATEWAY_ADDR = os.environ.get('PUSHGATEWAY_ADDR', 'pushgateway:9091')
PUSHGATEWAY_JOB = os.environ.get('PUSHGATEWAY_JOB', 'backup-palo-alto')
PUSHGATEWAY_INSTANCE = os.environ.get('PUSHGATEWAY_INSTANCE', HOST or 'unknown')


def get_full_configuration() -> bool:
    """Get Palo Alto API key, fetch running config, save to backup_file."""
    start_time = time.time()
    error_type = None

    if not all([HOST, USERNAME, PASSWORD]):
        print("❌ HOST, USERNAME, and PASSWORD must be set")
        if USE_METRICS:
            metrics.BACKUP_PALO_CONNECTION_FAILURE_TOTAL.labels(error_type='connection_error').inc()
            metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
        return False

    base_url = f"https://{HOST}:{PORT}" if PORT != '443' else f"https://{HOST}"
    api_base = f"{base_url}/api"

    try:
        print(f"Connecting to Palo Alto: {HOST}:{PORT}...")

        # Get API key
        key_url = f"{api_base}/?type=keygen&user={quote(USERNAME, safe='')}&password={quote(PASSWORD, safe='')}"
        try:
            key_resp = requests.get(key_url, verify=VERIFY_SSL, timeout=30)
            key_resp.raise_for_status()
        except requests.RequestException as e:
            resp = getattr(e, 'response', None)
            status = resp.status_code if resp is not None else None
            error_type = 'authentication_error' if status in (401, 403) else 'connection_error'
            if USE_METRICS:
                metrics.BACKUP_PALO_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            print(f"❌ API keygen failed: {e}")
            return False

        root = ET.fromstring(key_resp.text)
        key_elem = root.find('.//key')
        if key_elem is None or not key_elem.text:
            status = root.find('.//status')
            msg = root.find('.//msg')
            err = msg.text if msg is not None else key_resp.text[:500]
            if USE_METRICS:
                metrics.BACKUP_PALO_CONNECTION_FAILURE_TOTAL.labels(error_type='authentication_error').inc()
                metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            print(f"❌ API did not return a key: {err}")
            return False

        api_key = key_elem.text
        if USE_METRICS:
            metrics.BACKUP_PALO_CONNECTION_SUCCESS_TOTAL.inc()
        print("✅ Successfully authenticated to Palo Alto")

        # Fetch running config1
        try:
            values = {
                'type': 'op',
                'cmd': '<show><config><running></running></config></show>',
                'key': api_key,
            }
            config_resp = requests.post(f"{api_base}/", data=values, verify=VERIFY_SSL, timeout=60)
            config_resp.raise_for_status()
        except requests.RequestException as e:
            error_type = 'configuration_error'
            if USE_METRICS:
                metrics.BACKUP_PALO_CONFIGURATION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='configuration').set(time.time())
            print(f"❌ Failed to fetch running config: {e}")
            return False

        root = ET.fromstring(config_resp.text)
        if root.find('.//result') is None and root.find('.//response') is None:
            err = config_resp.text[:500] if config_resp.text else 'Unknown error'
            if USE_METRICS:
                metrics.BACKUP_PALO_CONFIGURATION_FAILURE_TOTAL.labels(error_type='configuration_error').inc()
                metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='configuration').set(time.time())
            print(f"❌ Invalid config response: {err}")
            return False

        with open(backup_file, 'w') as f:
            f.write(config_resp.text)

        print(f"✅ Configuration saved to: {backup_file}")
        if USE_METRICS:
            metrics.BACKUP_PALO_CONFIGURATION_SUCCESS_TOTAL.inc()
            metrics.BACKUP_PALO_LAST_SUCCESS_TIMESTAMP.labels(operation='configuration').set(time.time())
            duration = time.time() - start_time
            metrics.BACKUP_PALO_DURATION_SECONDS.labels(operation='configuration').observe(duration)
        return True

    except ET.ParseError as e:
        if USE_METRICS:
            metrics.BACKUP_PALO_CONNECTION_FAILURE_TOTAL.labels(error_type='api_error').inc()
            metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
        print(f"❌ Invalid API response (XML): {e}")
        return False
    except Exception as e:
        if error_type is None:
            error_type = 'unknown_error'
        if USE_METRICS:
            metrics.BACKUP_PALO_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
            metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
        print(f"❌ Error: {e}")
        return False


def backup_data() -> bool:
    """Upload backup file to cloud (AWS/Azure). If cloud disabled, skip and keep file locally."""
    start_time = time.time()

    if not cloud_upload.is_cloud_enabled():
        if os.path.exists(backup_file):
            file_path = os.path.abspath(backup_file)
            print("ℹ️  Cloud upload disabled. Backup file stored locally in container:")
            print(f"   📁 Path: {file_path}")
        else:
            print("⚠️  Cloud upload disabled and backup file not found.")
        return True

    success, file_size, error_type = cloud_upload.upload_backup(backup_file, "backup-palo-alto")

    if success:
        if USE_METRICS:
            metrics.BACKUP_PALO_S3_UPLOAD_SUCCESS_TOTAL.inc()
            metrics.BACKUP_PALO_LAST_SUCCESS_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            metrics.record_upload_success(file_size)
            duration = time.time() - start_time
            metrics.BACKUP_PALO_DURATION_SECONDS.labels(operation='s3_upload').observe(duration)
        return True

    if error_type:
        if USE_METRICS:
            metrics.BACKUP_PALO_S3_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            metrics.BACKUP_PALO_LAST_FAILURE_TIMESTAMP.labels(operation='s3_upload').set(time.time())
    return False


if __name__ == "__main__":
    overall_start_time = time.time()

    if USE_METRICS:
        metrics.init_failure_gauges()

    config_success = get_full_configuration()
    if config_success:
        cloud_success = backup_data()
    else:
        print("❌ Configuration retrieval failed. Skipping cloud upload.")
        cloud_success = False

    if USE_METRICS:
        overall_duration = time.time() - overall_start_time
        metrics.BACKUP_PALO_DURATION_SECONDS.labels(operation='total').observe(overall_duration)
        metrics.push_metrics(PUSHGATEWAY_ADDR, PUSHGATEWAY_JOB, PUSHGATEWAY_INSTANCE)
    else:
        print("ℹ️  Metrics disabled. Set metrics-pushgw=true to enable Prometheus metrics.")

    sys.exit(0 if (config_success and cloud_success) else 1)

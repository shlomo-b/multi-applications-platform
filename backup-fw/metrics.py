"""Prometheus metrics and Pushgateway push logic for Fortigate backup."""
import os
import re
import requests
from prometheus_client import CollectorRegistry, Gauge, Counter, Histogram, push_to_gateway

registry = CollectorRegistry()

BACKUP_CONNECTION_SUCCESS_TOTAL = Counter('backup_connection_success_total', 'Total number of successful firewall connections', registry=registry)
BACKUP_CONNECTION_FAILURE_TOTAL = Counter('backup_connection_failure_total', 'Total number of failed firewall connections', ['error_type'], registry=registry)
BACKUP_CONFIGURATION_SUCCESS_TOTAL = Counter('backup_configuration_success_total', 'Total number of successful configuration backups', registry=registry)
BACKUP_CONFIGURATION_FAILURE_TOTAL = Counter('backup_configuration_failure_total', 'Total number of failed configuration backups', ['error_type'], registry=registry)
BACKUP_S3_UPLOAD_SUCCESS_TOTAL = Counter('backup_s3_upload_success_total', 'Total number of successful S3 uploads', registry=registry)
BACKUP_S3_UPLOAD_FAILURE_TOTAL = Counter('backup_s3_upload_failure_total', 'Total number of failed S3 uploads', ['error_type'], registry=registry)
BACKUP_DURATION_SECONDS = Histogram('backup_duration_seconds', 'Duration of backup operation in seconds', ['operation'], registry=registry, buckets=[1, 5, 10, 30, 60, 120, 300, 600])
BACKUP_S3_LAST_FILE_SIZE_BYTES = Gauge('backup_s3_last_file_size_bytes', 'Size of the last file uploaded to S3 in bytes', registry=registry)
BACKUP_S3_TOTAL_BYTES_UPLOADED = Gauge('backup_s3_total_bytes_uploaded', 'Total bytes uploaded to S3 (sum of all files uploaded in this run)', registry=registry)

_total_bytes_uploaded_accumulator = 0
BACKUP_LAST_SUCCESS_TIMESTAMP = Gauge('backup_last_success_timestamp', 'Unix timestamp of last successful backup', ['operation'], registry=registry)
BACKUP_LAST_FAILURE_TIMESTAMP = Gauge('backup_last_failure_timestamp', 'Unix timestamp of last failed backup', ['operation'], registry=registry)


def record_upload_success(file_size: float) -> None:
    """Record a successful upload (update accumulator and gauge)."""
    global _total_bytes_uploaded_accumulator
    _total_bytes_uploaded_accumulator += file_size
    BACKUP_S3_LAST_FILE_SIZE_BYTES.set(file_size)
    BACKUP_S3_TOTAL_BYTES_UPLOADED.set(_total_bytes_uploaded_accumulator)


def init_failure_gauges() -> None:
    """Initialize all failure metrics to 0 so they are always visible in Pushgateway."""
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='authentication_error').inc(0)
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='ssh_error').inc(0)
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='connection_error').inc(0)
    BACKUP_CONFIGURATION_FAILURE_TOTAL.labels(error_type='configuration_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='file_not_found').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='missing_bucket_name').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='s3_client_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='upload_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='unknown_error').inc(0)


def push_metrics(pushgateway_addr: str, job: str, instance: str) -> None:
    """Push all metrics to Pushgateway, manually accumulating counters."""
    global _total_bytes_uploaded_accumulator
    try:
        print(f"✅ Job: {job}, Instance: {instance}")

        try:
            gateway_url = pushgateway_addr if pushgateway_addr.startswith(('http://', 'https://')) else f'http://{pushgateway_addr}'
            metrics_url = f"{gateway_url.rstrip('/')}/metrics"
            response = requests.get(metrics_url, timeout=15)
            response.raise_for_status()
            metrics_text = response.text

            def find_metric_value(metric_name: str):
                num = r'(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
                for order in (
                    rf'instance="{re.escape(instance)}",job="{re.escape(job)}"',
                    rf'job="{re.escape(job)}",instance="{re.escape(instance)}"',
                ):
                    pattern = rf'{re.escape(metric_name)}\{{{order}\}}\s+{num}'
                    match = re.search(pattern, metrics_text)
                    if match:
                        return float(match.group(1))
                return None

            counter_values = {}
            for metric_name in ['backup_connection_success_total', 'backup_configuration_success_total', 'backup_s3_upload_success_total']:
                val = find_metric_value(metric_name)
                if val is not None:
                    counter_values[metric_name] = val

            gauge_values = {}
            for metric_name in ['backup_s3_last_file_size_bytes', 'backup_s3_total_bytes_uploaded']:
                val = find_metric_value(metric_name)
                if val is not None:
                    gauge_values[metric_name] = val

            if counter_values:
                print(f"✅ Accumulating: found existing counters for job={job} instance={instance}")

            if 'backup_connection_success_total' in counter_values:
                current_val = BACKUP_CONNECTION_SUCCESS_TOTAL._value.get()
                BACKUP_CONNECTION_SUCCESS_TOTAL._value._value = counter_values['backup_connection_success_total'] + current_val
            if 'backup_configuration_success_total' in counter_values:
                current_val = BACKUP_CONFIGURATION_SUCCESS_TOTAL._value.get()
                BACKUP_CONFIGURATION_SUCCESS_TOTAL._value._value = counter_values['backup_configuration_success_total'] + current_val
            if 'backup_s3_upload_success_total' in counter_values:
                current_val = BACKUP_S3_UPLOAD_SUCCESS_TOTAL._value.get()
                BACKUP_S3_UPLOAD_SUCCESS_TOTAL._value._value = counter_values['backup_s3_upload_success_total'] + current_val

            if 'backup_s3_total_bytes_uploaded' in gauge_values:
                existing_total = gauge_values['backup_s3_total_bytes_uploaded']
                new_total = existing_total + _total_bytes_uploaded_accumulator
                BACKUP_S3_TOTAL_BYTES_UPLOADED.set(new_total)
            if 'backup_s3_last_file_size_bytes' in gauge_values and BACKUP_S3_LAST_FILE_SIZE_BYTES._value.get() == 0:
                BACKUP_S3_LAST_FILE_SIZE_BYTES.set(gauge_values['backup_s3_last_file_size_bytes'])

        except (requests.RequestException, AttributeError, ValueError) as e:
            print(f"⚠️ Could not fetch existing metrics from Pushgateway: {e}")

        push_to_gateway(
            gateway=pushgateway_addr,
            job=job,
            registry=registry,
            grouping_key={'instance': instance}
        )
        print(f"✅ Metrics pushed to Pushgateway at {pushgateway_addr}")
    except Exception as e:
        print(f"❌ Failed to push metrics to Pushgateway: {e}")

"""Fortigate backup: fetch full configuration and upload to cloud or keep locally."""
import os
import select
import sys
import time

import paramiko

import cloud_upload
import metrics

# Configuration
HOST = os.environ.get("HOST")
PORT = os.environ.get("PORT")
USERNAME = os.environ.get("USERNAME")
PASSWORD = os.environ.get("PASSWORD")
backup_file = "fortigate_backup.conf"
FW_NAME = os.environ.get("FW_NAME")

USE_METRICS = os.environ.get("metrics-pushgw", "false").lower() == "true"
PUSHGATEWAY_ADDR = os.environ.get("PUSHGATEWAY_ADDR", "pushgateway:9091")
PUSHGATEWAY_JOB = os.environ.get("PUSHGATEWAY_JOB", "backup-fw-fortigate")
PUSHGATEWAY_INSTANCE = os.environ.get("PUSHGATEWAY_INSTANCE", HOST or "unknown")


def get_full_configuration() -> bool:
    """Connect to Fortigate, run show full-configuration, save to backup_file."""
    start_time = time.time()
    error_type = None

    try:
        print(f"Connecting to: {HOST}:{PORT}...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(HOST, int(PORT), USERNAME, PASSWORD, timeout=10, allow_agent=False, look_for_keys=False)
            if USE_METRICS:
                metrics.BACKUP_CONNECTION_SUCCESS_TOTAL.inc()
            print("✅ The user successfully connected to: Fortigate")
        except paramiko.AuthenticationException:
            error_type = 'authentication_error'
            if USE_METRICS:
                metrics.BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise
        except paramiko.SSHException:
            error_type = 'ssh_error'
            if USE_METRICS:
                metrics.BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise
        except Exception:
            error_type = 'connection_error'
            if USE_METRICS:
                metrics.BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise

        try:
            shell = ssh.invoke_shell()
            time.sleep(1)
            shell.recv(65535)
            print("Command:📤 show full-configuration")
            shell.send("show full-configuration\n")

            with open(backup_file, 'w') as f:
                while True:
                    rlist, _, _ = select.select([shell], [], [], 1)
                    if shell in rlist:
                        chunk = shell.recv(99999).decode(errors='replace')
                        if "--More--" in chunk:
                            shell.send(" ")
                            chunk = chunk.replace("--More--", "")
                        f.write(chunk)
                        f.flush()
                        if FW_NAME in chunk:
                            break

            print(f"✅ Configuration saved to: {backup_file}")
            ssh.close()

            if USE_METRICS:
                metrics.BACKUP_CONFIGURATION_SUCCESS_TOTAL.inc()
                metrics.BACKUP_LAST_SUCCESS_TIMESTAMP.labels(operation='configuration').set(time.time())
                duration = time.time() - start_time
                metrics.BACKUP_DURATION_SECONDS.labels(operation='configuration').observe(duration)
            return True

        except Exception:
            error_type = 'configuration_error'
            if USE_METRICS:
                metrics.BACKUP_CONFIGURATION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='configuration').set(time.time())
            raise

    except Exception as e:
        if error_type is None:
            error_type = 'unknown_error'
        print(f" Error: ❌ {e}")
        return False


def backup_data() -> bool:
    """Upload backup file to cloud (AWS/Azure). If cloud disabled, skip and keep file locally."""
    start_time = time.time()

    if not cloud_upload.is_cloud_enabled():
        if os.path.exists(backup_file):
            file_path = os.path.abspath(backup_file)
            print(f"ℹ️  Cloud upload disabled. Backup file stored locally in container:")
            print(f"   📁 Path: {file_path}")
        else:
            print("⚠️  Cloud upload disabled and backup file not found.")
        return True  # Return True since file is kept locally (not an error)

    success, file_size, error_type = cloud_upload.upload_backup(backup_file, "backup-fw-fortigate")

    if success:
        if USE_METRICS:
            metrics.BACKUP_STORAGE_CLOUD_UPLOAD_SUCCESS_TOTAL.inc()
            metrics.BACKUP_LAST_SUCCESS_TIMESTAMP.labels(operation='storage_upload').set(time.time())
            metrics.record_upload_success(file_size)
            duration = time.time() - start_time
            metrics.BACKUP_DURATION_SECONDS.labels(operation='storage_upload').observe(duration)
        return True

    if error_type:
        if USE_METRICS:
            metrics.BACKUP_STORAGE_CLOUD_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            metrics.BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation="storage_upload").set(time.time())
    return False


def run_backup_once() -> bool:
    """Run a single backup cycle and push metrics (if enabled)."""
    overall_start_time = time.time()

    if USE_METRICS:
        metrics.init_failure_gauges(aws_enabled=cloud_upload.USE_AWS, azure_enabled=cloud_upload.USE_AZURE)

    config_success = get_full_configuration()
    if config_success:
        cloud_success = backup_data()
    else:
        print("❌ Configuration retrieval failed. Skipping cloud upload.")
        cloud_success = False

    if USE_METRICS:
        overall_duration = time.time() - overall_start_time
        metrics.BACKUP_DURATION_SECONDS.labels(operation="total").observe(overall_duration)
        metrics.push_metrics(PUSHGATEWAY_ADDR, PUSHGATEWAY_JOB, PUSHGATEWAY_INSTANCE)
    else:
        print("ℹ️  Metrics disabled. Set metrics-pushgw=true to enable Prometheus metrics.")

    return bool(config_success and cloud_success)


if __name__ == "__main__":
    cronjob_enabled = os.environ.get("CRONJOB_ENABLED", "false").lower() == "true"
    if cronjob_enabled:
        from cronjob import run_cron_loop

        run_cron_loop()
    else:
        success = run_backup_once()
        sys.exit(0 if success else 1)

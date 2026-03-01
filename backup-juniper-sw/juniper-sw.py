import paramiko
import time
import select
import os
import re
import sys

import cloud_upload
import metrics

# Config
HOST = os.environ.get('HOST')
PORT = os.environ.get('PORT')
USERNAME = os.environ.get('USERNAME')
PASSWORD = os.environ.get('PASSWORD')
backup_file = "juniper_backup.txt"
SW_NAME = os.environ.get('SW_NAME')
USE_METRICS = os.environ.get('metrics-pushgw', 'false').lower() == 'true'
PUSHGATEWAY_ADDR = os.environ.get('PUSHGATEWAY_ADDR', 'pushgateway:9091')
PUSHGATEWAY_JOB = os.environ.get('PUSHGATEWAY_JOB', 'backup-sw-juniper')
PUSHGATEWAY_INSTANCE = os.environ.get('PUSHGATEWAY_INSTANCE', HOST or 'unknown')


def get_full_configuration():
    start_time = time.time()
    error_type = None

    try:
        print(f"Connecting to: {HOST}:{PORT}...")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(HOST, int(PORT), USERNAME, PASSWORD, timeout=10, allow_agent=False, look_for_keys=False)
            if USE_METRICS:
                metrics.BACKUP_SW_CONNECTION_SUCCESS_TOTAL.inc()
            print(f"✅ The user successfully connected to: {SW_NAME}")
        except paramiko.AuthenticationException:
            error_type = 'authentication_error'
            if USE_METRICS:
                metrics.BACKUP_SW_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_SW_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise
        except paramiko.SSHException:
            error_type = 'ssh_error'
            if USE_METRICS:
                metrics.BACKUP_SW_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_SW_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise
        except Exception:
            error_type = 'connection_error'
            if USE_METRICS:
                metrics.BACKUP_SW_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_SW_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise

        shell = ssh.invoke_shell()
        time.sleep(2)
        shell.recv(65535)

        shell.send("cli\n")
        time.sleep(1)
        shell.recv(65535)

        shell.send("set cli screen-length 0\n")
        time.sleep(1)
        shell.recv(65535)

        shell.send("show configuration | display set\n")
        time.sleep(3)

        try:
            with open(backup_file, 'w') as f:
                while True:
                    rlist, _, _ = select.select([shell], [], [], 3)
                    if shell in rlist:
                        chunk = shell.recv(99999).decode(errors='replace')
                        chunk = re.sub(r' +', ' ', chunk)
                        chunk = chunk.strip()
                        chunk = "\n".join([line.strip() for line in chunk.split("\n") if line.strip()])
                        f.write(chunk + "\n")
                        f.flush()
                        if f"{USERNAME}{SW_NAME}" in chunk:
                            print(f"Detected prompt for user: {USERNAME}")
                            break

            print(f"✅ Configuration saved to: {backup_file}")
            ssh.close()

            if USE_METRICS:
                metrics.BACKUP_SW_CONFIGURATION_SUCCESS_TOTAL.inc()
                metrics.BACKUP_SW_LAST_SUCCESS_TIMESTAMP.labels(operation='configuration').set(time.time())
                duration = time.time() - start_time
                metrics.BACKUP_SW_DURATION_SECONDS.labels(operation='configuration').observe(duration)
            return True

        except Exception:
            error_type = 'configuration_error'
            if USE_METRICS:
                metrics.BACKUP_SW_CONFIGURATION_FAILURE_TOTAL.labels(error_type=error_type).inc()
                metrics.BACKUP_SW_LAST_FAILURE_TIMESTAMP.labels(operation='configuration').set(time.time())
            raise

    except Exception as e:
        if error_type is None:
            error_type = 'unknown_error'
        print(f" Error: ❌ {e}")
        return False


def backup_data():
    start_time = time.time()
    error_type = None

    if not cloud_upload.is_cloud_enabled():
        if os.path.exists(backup_file):
            file_path = os.path.abspath(backup_file)
            print(f"ℹ️  Cloud upload disabled. Backup file stored locally in container:")
            print(f"   📁 Path: {file_path}")
        else:
            print("⚠️  Cloud upload disabled and backup file not found.")
        return True  # Return True since file is kept locally (not an error)

    success, file_size, err_type = cloud_upload.upload_backup(backup_file, "backup-sw-juniper")
    error_type = err_type

    if success:
        if USE_METRICS:
            metrics.BACKUP_SW_S3_UPLOAD_SUCCESS_TOTAL.inc()
            metrics.BACKUP_SW_LAST_SUCCESS_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            metrics.record_upload_success(file_size)
            duration = time.time() - start_time
            metrics.BACKUP_SW_DURATION_SECONDS.labels(operation='s3_upload').observe(duration)
        return True

    if error_type:
        if USE_METRICS:
            metrics.BACKUP_SW_S3_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            metrics.BACKUP_SW_LAST_FAILURE_TIMESTAMP.labels(operation='s3_upload').set(time.time())
    print(f"❌ Cloud upload failed (error_type={error_type})")
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
        metrics.BACKUP_SW_DURATION_SECONDS.labels(operation='total').observe(overall_duration)
        metrics.push_metrics(PUSHGATEWAY_ADDR, PUSHGATEWAY_JOB, PUSHGATEWAY_INSTANCE)
    else:
        print("ℹ️  Metrics disabled. Set metrics-pushgw=true to enable Prometheus metrics.")

    sys.exit(0 if (config_success and cloud_success) else 1)

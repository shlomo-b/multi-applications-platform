import paramiko
import time
import select
import os
import boto3
import sys
import requests
from prometheus_client import CollectorRegistry, Gauge, Counter, Histogram, pushadd_to_gateway, push_to_gateway

registry = CollectorRegistry()

BACKUP_CONNECTION_SUCCESS_TOTAL = Counter('backup_connection_success_total', 'Total number of successful firewall connections', registry=registry)
BACKUP_CONNECTION_FAILURE_TOTAL = Counter('backup_connection_failure_total', 'Total number of failed firewall connections', ['error_type'], registry=registry)
BACKUP_CONFIGURATION_SUCCESS_TOTAL = Counter('backup_configuration_success_total', 'Total number of successful configuration backups', registry=registry)
BACKUP_CONFIGURATION_FAILURE_TOTAL = Counter('backup_configuration_failure_total', 'Total number of failed configuration backups', ['error_type'], registry=registry)
BACKUP_S3_UPLOAD_SUCCESS_TOTAL = Counter('backup_s3_upload_success_total', 'Total number of successful S3 uploads', registry=registry)
BACKUP_S3_UPLOAD_FAILURE_TOTAL = Counter('backup_s3_upload_failure_total', 'Total number of failed S3 uploads', ['error_type'], registry=registry)
BACKUP_DURATION_SECONDS = Histogram('backup_duration_seconds', 'Duration of backup operation in seconds', ['operation'], registry=registry, buckets=[1, 5, 10, 30, 60, 120, 300, 600])
# S3 Upload Size Metrics
BACKUP_S3_LAST_FILE_SIZE_BYTES = Gauge('backup_s3_last_file_size_bytes', 'Size of the last file uploaded to S3 in bytes', registry=registry)
BACKUP_S3_TOTAL_BYTES_UPLOADED = Gauge('backup_s3_total_bytes_uploaded', 'Total bytes uploaded to S3 (sum of all files uploaded in this run)', registry=registry)

# Track total bytes across all runs (module-level variable)
_total_bytes_uploaded_accumulator = 0
BACKUP_LAST_SUCCESS_TIMESTAMP = Gauge('backup_last_success_timestamp', 'Unix timestamp of last successful backup', ['operation'], registry=registry)
BACKUP_LAST_FAILURE_TIMESTAMP = Gauge('backup_last_failure_timestamp', 'Unix timestamp of last failed backup', ['operation'], registry=registry)

# Configuration
HOST = os.environ.get('HOST')
PORT = os.environ.get('PORT')
USERNAME = os.environ.get('USERNAME')
PASSWORD = os.environ.get('PASSWORD')
backup_file = "fortigate_backup.conf"
FW_NAME = os.environ.get('FW_NAME')
# AWS credentials
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')

# Pushgateway configuration
PUSHGATEWAY_ADDR = os.environ.get('PUSHGATEWAY_ADDR', 'pushgateway:9091')
PUSHGATEWAY_JOB = os.environ.get('PUSHGATEWAY_JOB', 'backup-fw')
PUSHGATEWAY_INSTANCE = os.environ.get('PUSHGATEWAY_INSTANCE', HOST or 'unknown')

def get_full_configuration():
    start_time = time.time()
    error_type = None
    
    try:
        print(f"Connecting to: {HOST}:{PORT}...")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Track connection attempt
        try:
            ssh.connect(HOST, int(PORT), USERNAME, PASSWORD, timeout=10, allow_agent=False, look_for_keys=False)
            BACKUP_CONNECTION_SUCCESS_TOTAL.inc()
            print(f"✅ The user successfully connected to: {'Fortigate'}")
        except paramiko.AuthenticationException as e:
            error_type = 'authentication_error'
            BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise
        except paramiko.SSHException as e:
            error_type = 'ssh_error'
            BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise
        except Exception as e:
            error_type = 'connection_error'
            BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='connection').set(time.time())
            raise

        # Get configuration
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
            
            # Record success metrics
            BACKUP_CONFIGURATION_SUCCESS_TOTAL.inc()
            BACKUP_LAST_SUCCESS_TIMESTAMP.labels(operation='configuration').set(time.time())
            
            # File size will be tracked when uploaded to S3
            
            # Record duration
            duration = time.time() - start_time
            BACKUP_DURATION_SECONDS.labels(operation='configuration').observe(duration)
            
            return True

        except Exception as e:
            error_type = 'configuration_error'
            BACKUP_CONFIGURATION_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='configuration').set(time.time())
            raise

    except Exception as e:
        if error_type is None:
            error_type = 'unknown_error'
        print(f" Error: ❌ {e}")
        return False
 
# Function to upload the backup file to S3
def backup_data():
    global _total_bytes_uploaded_accumulator
    start_time = time.time()
    error_type = None
    
    try:
        # Check if the file exists
        if not os.path.exists(backup_file):
            error_type = 'file_not_found'
            BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            print(f"❌ Backup file '{backup_file}' not found.")
            return False

        # Define S3 bucket details
        BUCKET_NAME = os.environ.get('BUCKET_NAME')
        if not BUCKET_NAME:
            error_type = 'missing_bucket_name'
            BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            print(f"❌ BUCKET_NAME environment variable not set.")
            return False

        s3_object_name = f"backups/{backup_file}"

        # Create a Boto3 S3 client
        try:
            s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        except Exception as e:
            error_type = 's3_client_error'
            BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            raise

        # Upload the file to S3
        try:
            # Get file size before uploading
            file_size = os.path.getsize(backup_file)
            
            s3.upload_file(backup_file, BUCKET_NAME, s3_object_name)
            print(f"✅ Backup file: {backup_file}, successfully uploaded to S3 bucket: {BUCKET_NAME}")
            
            # Record success metrics
            BACKUP_S3_UPLOAD_SUCCESS_TOTAL.inc()
            BACKUP_LAST_SUCCESS_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            
            # Record the size of the last file uploaded to S3
            BACKUP_S3_LAST_FILE_SIZE_BYTES.set(file_size)
            
            # Track total bytes uploaded (accumulates across all runs)
            _total_bytes_uploaded_accumulator += file_size
            BACKUP_S3_TOTAL_BYTES_UPLOADED.set(_total_bytes_uploaded_accumulator)
            
            # Record duration
            duration = time.time() - start_time
            BACKUP_DURATION_SECONDS.labels(operation='s3_upload').observe(duration)
            
            return True

        except Exception as e:
            error_type = 'upload_error'
            BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type=error_type).inc()
            BACKUP_LAST_FAILURE_TIMESTAMP.labels(operation='s3_upload').set(time.time())
            raise

    except Exception as e:
        if error_type is None:
            error_type = 'unknown_error'
        print(f"❌ Error during S3 upload: {e}")
        return False

def push_metrics():
    """Push all metrics to Pushgateway, manually accumulating counters"""
    import requests
    import re
    try:
        print(f"✅ Job: {PUSHGATEWAY_JOB}, Instance: {PUSHGATEWAY_INSTANCE}")
        
        # Get current counter values from Pushgateway and accumulate them
        try:
            metrics_url = f"{PUSHGATEWAY_ADDR}/metrics"
            response = requests.get(metrics_url, timeout=15)
            response.raise_for_status()
            metrics_text = response.text

            # Pushgateway may expose labels as instance,job or job,instance
            def find_metric_value(metric_name):
                num = r'(\d+(?:\.\d+)?)'
                for order in (
                    rf'instance="{re.escape(PUSHGATEWAY_INSTANCE)}",job="{re.escape(PUSHGATEWAY_JOB)}"',
                    rf'job="{re.escape(PUSHGATEWAY_JOB)}",instance="{re.escape(PUSHGATEWAY_INSTANCE)}"',
                ):
                    pattern = rf'{re.escape(metric_name)}\{{{order}\}}\s+{num}'
                    match = re.search(pattern, metrics_text)
                    if match:
                        return float(match.group(1))
                return None

            counter_values = {}
            for metric_name in ['backup_connection_success_total', 'backup_configuration_success_total',
                               'backup_s3_upload_success_total']:
                val = find_metric_value(metric_name)
                if val is not None:
                    counter_values[metric_name] = val

            gauge_values = {}
            for metric_name in ['backup_s3_last_file_size_bytes', 'backup_s3_total_bytes_uploaded']:
                val = find_metric_value(metric_name)
                if val is not None:
                    gauge_values[metric_name] = val

            if counter_values:
                print(f"✅ Accumulating: found existing counters for job={PUSHGATEWAY_JOB} instance={PUSHGATEWAY_INSTANCE}")

            # Add current counter values to the existing ones
            if 'backup_connection_success_total' in counter_values:
                current_val = BACKUP_CONNECTION_SUCCESS_TOTAL._value.get()
                BACKUP_CONNECTION_SUCCESS_TOTAL._value._value = counter_values['backup_connection_success_total'] + current_val
            
            if 'backup_configuration_success_total' in counter_values:
                current_val = BACKUP_CONFIGURATION_SUCCESS_TOTAL._value.get()
                BACKUP_CONFIGURATION_SUCCESS_TOTAL._value._value = counter_values['backup_configuration_success_total'] + current_val
            
            if 'backup_s3_upload_success_total' in counter_values:
                current_val = BACKUP_S3_UPLOAD_SUCCESS_TOTAL._value.get()
                BACKUP_S3_UPLOAD_SUCCESS_TOTAL._value._value = counter_values['backup_s3_upload_success_total'] + current_val
            
            # Accumulate total bytes uploaded (add existing value to current accumulator)
            if 'backup_s3_total_bytes_uploaded' in gauge_values:
                global _total_bytes_uploaded_accumulator
                existing_total = gauge_values['backup_s3_total_bytes_uploaded']
                new_total = existing_total + _total_bytes_uploaded_accumulator
                BACKUP_S3_TOTAL_BYTES_UPLOADED.set(new_total)
            
            # Keep last file size as is (it's already set to current file size in backup_data)
            # But if no file was uploaded this run, preserve the last value from Pushgateway
            if 'backup_s3_last_file_size_bytes' in gauge_values and BACKUP_S3_LAST_FILE_SIZE_BYTES._value.get() == 0:
                BACKUP_S3_LAST_FILE_SIZE_BYTES.set(gauge_values['backup_s3_last_file_size_bytes'])
                
        except (requests.RequestException, AttributeError, ValueError) as e:
            # GET failed or parse failed: push current run only (counter won't accumulate this run)
            print(f"⚠️ Could not fetch existing metrics from Pushgateway: {e}")
            pass

        # Always use push_to_gateway to replace with accumulated values
        push_to_gateway(
            gateway=PUSHGATEWAY_ADDR,
            job=PUSHGATEWAY_JOB,
            registry=registry,
            grouping_key={'instance': PUSHGATEWAY_INSTANCE}
        )
        
        print(f"✅ Metrics pushed to Pushgateway at {PUSHGATEWAY_ADDR}")
    except Exception as e:
        print(f"❌ Failed to push metrics to Pushgateway: {e}")

def run_backup():
    """Run a single backup cycle"""
    overall_start_time = time.time()
    
    # Initialize all failure metrics to 0 to ensure they're always visible
    # This ensures all metrics are pushed even when there are no failures
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='authentication_error').inc(0)
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='ssh_error').inc(0)
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='connection_error').inc(0)
    BACKUP_CONFIGURATION_FAILURE_TOTAL.labels(error_type='configuration_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='file_not_found').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='missing_bucket_name').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='s3_client_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='upload_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='unknown_error').inc(0)
    
    # Execute backup process
    config_success = get_full_configuration()
    
    if config_success:
        s3_success = backup_data()
    else:
        print("❌ Configuration retrieval failed. Skipping S3 upload.")
        s3_success = False
    
    # Record overall backup duration
    overall_duration = time.time() - overall_start_time
    BACKUP_DURATION_SECONDS.labels(operation='total').observe(overall_duration)
    
    # Push all metrics to Pushgateway (all metrics will be visible, even with 0 values)
    push_metrics()
    
    return config_success and s3_success

if __name__ == "__main__":
    overall_start_time = time.time()
    
    # Initialize all failure metrics to 0 to ensure they're always visible
    # This ensures all metrics are pushed even when there are no failures
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='authentication_error').inc(0)
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='ssh_error').inc(0)
    BACKUP_CONNECTION_FAILURE_TOTAL.labels(error_type='connection_error').inc(0)
    BACKUP_CONFIGURATION_FAILURE_TOTAL.labels(error_type='configuration_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='file_not_found').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='missing_bucket_name').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='s3_client_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='upload_error').inc(0)
    BACKUP_S3_UPLOAD_FAILURE_TOTAL.labels(error_type='unknown_error').inc(0)
    
    # Execute backup process
    config_success = get_full_configuration()
    
    if config_success:
        s3_success = backup_data()
    else:
        print("❌ Configuration retrieval failed. Skipping S3 upload.")
        s3_success = False
    
    # Record overall backup duration
    overall_duration = time.time() - overall_start_time
    BACKUP_DURATION_SECONDS.labels(operation='total').observe(overall_duration)
    
    # Push all metrics to Pushgateway (all metrics will be visible, even with 0 values)
    push_metrics()
    
    # Exit with appropriate code
    sys.exit(0 if (config_success and s3_success) else 1)

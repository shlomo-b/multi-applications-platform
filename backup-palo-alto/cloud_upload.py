"""Upload backup file to AWS S3, Azure Blob Storage, and/or GCP Cloud Storage (multi-cloud in parallel)."""
import os
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

USE_AWS = os.environ.get('aws', 'false').lower() == 'true'
USE_AZURE = os.environ.get('azure', 'false').lower() == 'true'
USE_GCP = os.environ.get('gcp', 'false').lower() == 'true'

if USE_AWS:
    import boto3
if USE_AZURE:
    from azure.identity import ClientSecretCredential, DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
if USE_GCP:
    from google.cloud import storage

logger = logging.getLogger(__name__)


def _upload_aws(backup_file: str, object_name: str) -> Tuple[bool, Optional[str]]:
    bucket = os.environ.get('BUCKET_NAME')
    if not bucket:
        return False, 'missing_bucket_name'
    try:
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        if access_key and secret_key:
            s3 = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        else:
            s3 = boto3.client('s3')
        s3.upload_file(backup_file, bucket, object_name)
        logger.info("Backup file %s uploaded to AWS S3 bucket: %s", backup_file, bucket)
        return True, None
    except Exception as e:
        error_type = 's3_client_error' if 'client' in str(e).lower() else 'upload_error'
        logger.exception("Error during AWS S3 upload: %s", e)
        return False, error_type


def _upload_azure(backup_file: str, object_name: str) -> Tuple[bool, Optional[str]]:
    account = os.environ.get('AZURE_STORAGE_ACCOUNT')
    container_name = os.environ.get('AZURE_STORAGE_CONTAINER')
    tenant_id = os.environ.get('AZURE_TENANT_ID')
    client_id = os.environ.get('AZURE_CLIENT_ID')
    client_secret = os.environ.get('AZURE_CLIENT_SECRET')
    if not account or not container_name:
        return False, 'missing_azure_config'
    try:
        if tenant_id and client_id and client_secret:
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
        else:
            credential = DefaultAzureCredential()
        account_url = f"https://{account}.blob.core.windows.net"
        blob_service = BlobServiceClient(account_url=account_url, credential=credential)
        container_client = blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(object_name)
        with open(backup_file, 'rb') as f:
            blob_client.upload_blob(f, overwrite=True)
        logger.info("Backup file %s uploaded to Azure Blob container: %s", backup_file, container_name)
        return True, None
    except Exception as e:
        error_type = (
            'azure_client_error'
            if 'credential' in str(e).lower() or 'blob' in str(e).lower()
            else 'upload_error'
        )
        logger.exception("Error during Azure Blob upload: %s", e)
        return False, error_type


def _upload_gcp(backup_file: str, object_name: str) -> Tuple[bool, Optional[str]]:
    bucket_name = os.environ.get('GCP_BUCKET_NAME') or os.environ.get('GCS_BUCKET_NAME')
    if not bucket_name:
        return False, 'missing_gcp_config'

    creds_value = os.environ.get('GCP_APPLICATION_CREDENTIALS') or os.environ.get(
        'GOOGLE_APPLICATION_CREDENTIALS'
    )

    client = None
    if creds_value:
        if os.path.isfile(creds_value):
            try:
                client = storage.Client.from_service_account_json(creds_value)
            except Exception as e:
                logger.exception("GCP credentials (file) error: %s", e)
                return False, 'gcp_client_error'
        else:
            try:
                info = json.loads(creds_value)
                client = storage.Client.from_service_account_info(info)
            except Exception as e:
                logger.exception("GCP credentials (JSON env) error: %s", e)
                return False, 'gcp_client_error'
    else:
        try:
            client = storage.Client()
        except Exception as e:
            logger.exception("GCP default credentials error: %s", e)
            return False, 'gcp_client_error'

    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        blob.upload_from_filename(backup_file)
        logger.info("Backup uploaded to GCP bucket: %s", bucket_name)
        return True, None
    except Exception as e:
        error_type = (
            'gcp_client_error'
            if 'google' in str(e).lower() or 'credentials' in str(e).lower()
            else 'upload_error'
        )
        logger.exception("GCP upload error: %s", e)
        return False, error_type


def upload_backup(backup_file: str, folder_prefix: str) -> Tuple[bool, float, Optional[str]]:
    """
    Upload backup file to every enabled cloud (aws / azure / gcp) in parallel.
    Returns (success, file_size, error_type).
    Success is True only if all enabled uploads succeed. On partial failure the local file is kept.
    """
    if not USE_AWS and not USE_AZURE and not USE_GCP:
        return False, 0.0, None

    if not os.path.exists(backup_file):
        return False, 0.0, 'file_not_found'

    base_name, ext = os.path.splitext(os.path.basename(backup_file))
    date_part = time.strftime("%Y-%m-%d")
    time_part = time.strftime("%H%M%S")
    object_name = f"{folder_prefix}/{base_name}_{date_part}_{time_part}{ext}"
    file_size = os.path.getsize(backup_file)

    tasks: List[Tuple[str, Callable[[], Tuple[bool, Optional[str]]]]] = []
    if USE_AWS:
        tasks.append(('aws', lambda: _upload_aws(backup_file, object_name)))
    if USE_AZURE:
        tasks.append(('azure', lambda: _upload_azure(backup_file, object_name)))
    if USE_GCP:
        tasks.append(('gcp', lambda: _upload_gcp(backup_file, object_name)))

    if not tasks:
        return False, float(file_size), None

    results: Dict[str, Tuple[bool, Optional[str]]] = {}
    max_workers = min(len(tasks), 3)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_label = {executor.submit(fn): label for label, fn in tasks}
        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                ok, err = future.result()
                results[label] = (ok, err)
            except Exception as e:
                logger.exception("Cloud upload task %s crashed: %s", label, e)
                results[label] = (False, 'upload_error')

    failures = [(label, err) for label, (ok, err) in results.items() if not ok]
    if failures:
        for label, err in failures:
            logger.error("Cloud upload failed for %s: %s", label, err)
        first_err = failures[0][1]
        return False, float(file_size), first_err

    try:
        os.remove(backup_file)
    except OSError:
        pass
    return True, float(file_size), None


def is_cloud_enabled() -> bool:
    """Return True if at least one cloud provider (aws/azure/gcp) is enabled."""
    return USE_AWS or USE_AZURE or USE_GCP

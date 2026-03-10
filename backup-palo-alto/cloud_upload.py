"""Upload backup file to AWS S3, Azure Blob Storage, or GCP Cloud Storage."""
import os
import json
import logging
import time
from typing import Optional, Tuple

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


def upload_backup(backup_file: str, folder_prefix: str) -> Tuple[bool, float, Optional[str]]:
    """Upload backup to cloud. Returns (success, file_size, error_type). On success, deletes local file."""
    if not USE_AWS and not USE_AZURE and not USE_GCP:
        return False, 0.0, None
    if not os.path.exists(backup_file):
        return False, 0.0, 'file_not_found'

    base_name, ext = os.path.splitext(os.path.basename(backup_file))
    date_part = time.strftime("%Y-%m-%d")
    time_part = time.strftime("%H%M%S")
    object_name = f"{folder_prefix}/{base_name}_{date_part}_{time_part}{ext}"
    file_size = os.path.getsize(backup_file)

    if USE_AWS:
        bucket = os.environ.get('BUCKET_NAME')
        if not bucket:
            return False, 0.0, 'missing_bucket_name'
        try:
            access_key = os.environ.get('AWS_ACCESS_KEY_ID')
            secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
            if access_key and secret_key:
                s3 = boto3.client('s3', aws_access_key_id=access_key, aws_secret_access_key=secret_key)
            else:
                # Fall back to default credentials (e.g. IAM role / IRSA / env / shared config)
                s3 = boto3.client('s3')
            s3.upload_file(backup_file, bucket, object_name)
            logger.info("Backup uploaded to AWS S3 bucket: %s", bucket)
            try:
                os.remove(backup_file)
            except OSError:
                pass
            return True, float(file_size), None
        except Exception as e:
            error_type = 's3_client_error' if 'client' in str(e).lower() else 'upload_error'
            logger.exception("AWS S3 upload error: %s", e)
            return False, 0.0, error_type

    if USE_AZURE:
        account = os.environ.get('AZURE_STORAGE_ACCOUNT')
        container_name = os.environ.get('AZURE_STORAGE_CONTAINER')
        tenant_id = os.environ.get('AZURE_TENANT_ID')
        client_id = os.environ.get('AZURE_CLIENT_ID')
        client_secret = os.environ.get('AZURE_CLIENT_SECRET')
        if not account or not container_name:
            return False, 0.0, 'missing_azure_config'
        try:
            if tenant_id and client_id and client_secret:
                credential = ClientSecretCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            else:
                # Fall back to default Azure credential (Managed Identity / federated SA / env)
                credential = DefaultAzureCredential()
            blob_service = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=credential)
            blob_client = blob_service.get_container_client(container_name).get_blob_client(object_name)
            with open(backup_file, 'rb') as f:
                blob_client.upload_blob(f, overwrite=True)
            logger.info("Backup uploaded to Azure Blob container: %s", container_name)
            try:
                os.remove(backup_file)
            except OSError:
                pass
            return True, float(file_size), None
        except Exception as e:
            error_type = 'azure_client_error' if 'credential' in str(e).lower() or 'blob' in str(e).lower() else 'upload_error'
            logger.exception("Azure Blob upload error: %s", e)
            return False, 0.0, error_type

    if USE_GCP:
        bucket_name = os.environ.get('GCP_BUCKET_NAME') or os.environ.get('GCS_BUCKET_NAME')
        if not bucket_name:
            return False, 0.0, 'missing_gcp_config'

        creds_value = os.environ.get('GCP_APPLICATION_CREDENTIALS') or os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')

        client = None
        if creds_value:
            # First, treat value as a path to a JSON file (Docker / volume / Secret volume)
            if os.path.isfile(creds_value):
                try:
                    client = storage.Client.from_service_account_json(creds_value)
                except Exception as e:
                    logger.exception("GCP credentials (file) error: %s", e)
                    return False, 0.0, 'gcp_client_error'
            else:
                # Otherwise, treat value as raw JSON content from env (e.g. K8s Secret -> env)
                try:
                    info = json.loads(creds_value)
                    client = storage.Client.from_service_account_info(info)
                except Exception as e:
                    logger.exception("GCP credentials (JSON env) error: %s", e)
                    return False, 0.0, 'gcp_client_error'
        else:
            # Fall back to default credentials (e.g. GKE Workload Identity / node SA)
            try:
                client = storage.Client()
            except Exception as e:
                logger.exception("GCP default credentials error: %s", e)
                return False, 0.0, 'gcp_client_error'

        try:
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            blob.upload_from_filename(backup_file)
            logger.info("Backup uploaded to GCP bucket: %s", bucket_name)
            try:
                os.remove(backup_file)
            except OSError:
                pass
            return True, float(file_size), None
        except Exception as e:
            error_type = 'gcp_client_error' if 'google' in str(e).lower() or 'credentials' in str(e).lower() else 'upload_error'
            logger.exception("GCP upload error: %s", e)
            return False, 0.0, error_type

    return False, 0.0, None


def is_cloud_enabled() -> bool:
    return USE_AWS or USE_AZURE or USE_GCP

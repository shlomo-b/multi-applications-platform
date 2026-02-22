"""Upload backup file to AWS S3 or Azure Blob Storage."""
import os
import time
from typing import Optional, Tuple

USE_AWS = os.environ.get('aws', 'false').lower() == 'true'
USE_AZURE = os.environ.get('azure', 'false').lower() == 'true'

if USE_AWS:
    import boto3
if USE_AZURE:
    from azure.identity import ClientSecretCredential
    from azure.storage.blob import BlobServiceClient


def upload_backup(backup_file: str, folder_prefix: str) -> Tuple[bool, float, Optional[str]]:
    """
    Upload backup file to cloud (AWS S3 or Azure).
    Returns (success, file_size, error_type).
    On success, deletes the local file. On failure, error_type is set.
    """
    if not USE_AWS and not USE_AZURE:
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
            s3 = boto3.client(
                's3',
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            )
            s3.upload_file(backup_file, bucket, object_name)
            print(f"✅ Backup file: {backup_file}, successfully uploaded to AWS S3 bucket: {bucket}")
            try:
                os.remove(backup_file)
            except OSError:
                pass
            return True, float(file_size), None
        except Exception as e:
            error_type = 's3_client_error' if 'client' in str(e).lower() else 'upload_error'
            print(f"❌ Error during AWS S3 upload: {e}")
            return False, 0.0, error_type

    if USE_AZURE:
        account = os.environ.get('AZURE_STORAGE_ACCOUNT')
        container_name = os.environ.get('AZURE_STORAGE_CONTAINER')
        tenant_id = os.environ.get('AZURE_TENANT_ID')
        client_id = os.environ.get('AZURE_CLIENT_ID')
        client_secret = os.environ.get('AZURE_CLIENT_SECRET')
        if not all([account, container_name, tenant_id, client_id, client_secret]):
            return False, 0.0, 'missing_azure_config'
        try:
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            account_url = f"https://{account}.blob.core.windows.net"
            blob_service = BlobServiceClient(account_url=account_url, credential=credential)
            container_client = blob_service.get_container_client(container_name)
            blob_client = container_client.get_blob_client(object_name)
            with open(backup_file, 'rb') as f:
                blob_client.upload_blob(f, overwrite=True)
            print(f"✅ Backup file: {backup_file}, successfully uploaded to Azure Blob container: {container_name}")
            try:
                os.remove(backup_file)
            except OSError:
                pass
            return True, float(file_size), None
        except Exception as e:
            error_type = 'azure_client_error' if 'credential' in str(e).lower() or 'blob' in str(e).lower() else 'upload_error'
            print(f"❌ Error during Azure Blob upload: {e}")
            return False, 0.0, error_type

    return False, 0.0, None


def is_cloud_enabled() -> bool:
    """Return True if at least one cloud provider (aws/azure) is enabled."""
    return USE_AWS or USE_AZURE

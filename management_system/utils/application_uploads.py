from logging import getLogger
from django.conf import settings
from .storage_operations import upload_to_bucket

logger = getLogger(__name__)

def upload_application_file(cloud_client, bucket_name, user_id, file_obj, file_type):
    prefix = f"applications/{user_id}/{file_type}"
    key = f"{prefix}/{file_obj.name}"
    success = upload_to_bucket(cloud_client, bucket_name, file_obj, key)
    if success:
        return key
    logger.error(f"Failed to upload {file_type} for user {user_id}")
    return None

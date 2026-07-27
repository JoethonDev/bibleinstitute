"""
Storage operations for file management.
Provides common interface for file operations with R2/Cloud storage.
"""
import io
import os
from typing import Optional
from urllib.parse import unquote
from logging import getLogger
from management_system.utils.r2_filters import R2FileFilter, FileFilterConfig

logger = getLogger(__name__)


def list_current_folder(cloud_client, bucket_name, folder_name="", filter_config: Optional[FileFilterConfig] = None, folders_only=False):
    """
    List files and folders in R2 storage with optional filtering.
    Uses a single ListObjectsV2 call (no brute-force pagination) for fast navigation.
    
    The ``folder_name`` param is an R2 key prefix — a slash-delimited path.
    (Previously it used ``-`` as a segment separator, which broke on any
    folder name containing a hyphen.)
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        folder_name: Slash-delimited folder prefix from the URL
        filter_config: Optional FileFilterConfig for filtering results
        folders_only: If True, only return folders (no files)
    
    Returns:
        Tuple of (contents list, parent_folder path)
    """
    if folder_name:
        folder_name = unquote(folder_name)
    
    # Compute parent by removing the last path segment
    parent_folder = None
    if folder_name and '/' in folder_name.rstrip('/'):
        parent_folder = folder_name.rstrip('/').rsplit('/', 1)[0] + '/'
    
    # Normalise trailing slash
    if folder_name and not folder_name.endswith("/"):
        folder_name += "/"
    
    response = cloud_client.list_objects_v2(
        Bucket=bucket_name,
        Prefix=folder_name,
        Delimiter="/",
        MaxKeys=1000,
    )
    
    contents = []
    
    file_filter = R2FileFilter(filter_config) if filter_config else None
    
    # Files – only process when not folders_only
    if "Contents" in response and not folders_only:
        for obj in response["Contents"]:
            key = obj["Key"]
            rel_path = key[len(folder_name):] if folder_name else key
            if rel_path and "/" not in rel_path.rstrip("/"):
                file_name = key.split("/")[-1]
                
                file_obj = {
                    "id": key,
                    "name": file_name,
                    "type": "file",
                    "size": obj.get("Size", 0),
                    "last_modified": obj.get("LastModified"),
                }
                
                if file_filter:
                    if file_filter.should_include_file(file_obj):
                        contents.append(file_obj)
                else:
                    if not file_name.endswith(".ts"):
                        contents.append(file_obj)
    
    # Folders — use the raw R2 prefix as the folder ID (no hyphen encoding)
    if "CommonPrefixes" in response:
        for folder in response["CommonPrefixes"]:
            prefix = folder["Prefix"]
            name = prefix.rstrip('/').rsplit('/', 1)[-1]
            folder_obj = {
                "id": prefix,
                "name": name,
                "type": "folder"
            }
            if not file_filter or file_filter.should_include_file(folder_obj):
                contents.insert(0, folder_obj)
    
    return contents, parent_folder


def download_from_bucket(cloud_client, bucket_name, file_name):
    """
    Download a file from R2 bucket to memory.
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        file_name: Key/path of the file in the bucket
    
    Returns:
        BytesIO object containing file data
    """
    try:
        in_memory = io.BytesIO()
        response = cloud_client.get_object(Bucket=bucket_name, Key=file_name)
        # Write incoming data
        in_memory.write(response['Body'].read())
        in_memory.seek(0)
        
        logger.info(f"Downloaded file from bucket: {file_name}")
        return in_memory
        
    except Exception as e:
        logger.error(f"Error downloading file from bucket: {e}")
        return None


def generate_unique_url(cloud_client, bucket_name, segment_name, expires_in=3600):
    """
    Generate a presigned URL for temporary file access.
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        segment_name: Key/path of the file in the bucket
        expires_in: Expiration time in seconds (default: 3600 = 1 hour)
    
    Returns:
        Presigned URL string
    """
    try:
        url = cloud_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': segment_name},
            ExpiresIn=expires_in
        )
        
        logger.debug(f"Generated presigned URL for: {segment_name}")
        return url
        
    except Exception as e:
        logger.error(f"Error generating presigned URL: {e}")
        return None


def upload_to_bucket(cloud_client, bucket_name, file_data, file_key, content_type=None):
    """
    Upload a file to R2 bucket.
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        file_data: File data (BytesIO or file-like object)
        file_key: Key/path for the file in the bucket
        content_type: MIME type of the file (optional)
    
    Returns:
        Boolean indicating success
    """
    try:
        extra_args = {}
        if content_type:
            extra_args['ContentType'] = content_type
        
        cloud_client.upload_fileobj(
            file_data,
            bucket_name,
            file_key,
            ExtraArgs=extra_args
        )
        
        logger.info(f"Uploaded file to bucket: {file_key}")
        return True
        
    except Exception as e:
        logger.error(f"Error uploading file to bucket: {e}")
        return False


def delete_from_bucket(cloud_client, bucket_name, file_key):
    """
    Delete a file from R2 bucket.
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        file_key: Key/path of the file to delete
    
    Returns:
        Boolean indicating success
    """
    try:
        cloud_client.delete_object(Bucket=bucket_name, Key=file_key)
        logger.info(f"Deleted file from bucket: {file_key}")
        return True
        
    except Exception as e:
        logger.error(f"Error deleting file from bucket: {e}")
        return False


def check_file_exists(cloud_client, bucket_name, file_key):
    """
    Check if a file exists in R2 bucket.
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        file_key: Key/path of the file to check
    
    Returns:
        Boolean indicating if file exists
    """
    try:
        cloud_client.head_object(Bucket=bucket_name, Key=file_key)
        return True
    except:
        return False


def get_file_metadata(cloud_client, bucket_name, file_key):
    """
    Get metadata for a file in R2 bucket.
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        file_key: Key/path of the file
    
    Returns:
        Dictionary containing file metadata or None
    """
    try:
        response = cloud_client.head_object(Bucket=bucket_name, Key=file_key)
        
        metadata = {
            'size': response.get('ContentLength', 0),
            'last_modified': response.get('LastModified'),
            'content_type': response.get('ContentType'),
            'etag': response.get('ETag', '').strip('"'),
        }
        
        return metadata
        
    except Exception as e:
        logger.error(f"Error getting file metadata: {e}")
        return None

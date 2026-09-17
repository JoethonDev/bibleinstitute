"""
Storage operations for file management.
Provides common interface for file operations with R2/Cloud storage.
"""
import io
import os
from typing import Optional
from urllib.parse import unquote
from logging import getLogger

import boto3
from django.conf import settings

from management_system.utils.r2_filters import R2FileFilter, FileFilterConfig, file_extension, file_kind

logger = getLogger(__name__)

# Search scan budget: the provider has no server-side name filter, so one
# request may inspect at most this many provider entries before returning the
# matches found so far (plus a continuation token to keep scanning).
SEARCH_PROVIDER_PAGE_SIZE = 1000
SEARCH_SCAN_PAGES = 5
SEARCH_RESULT_LIMIT = 200


def _file_item(key: str, *, prefix: str, size: int, last_modified) -> dict:
    """Build one canonical file item shared by browse, search, and the API."""
    rel_path = key[len(prefix):] if prefix else key
    file_name = key.rsplit("/", 1)[-1]
    extension = file_extension(file_name)
    return {
        "id": key,
        "name": file_name,
        "type": "file",
        "size": size,
        "last_modified": last_modified,
        "kind": file_kind(file_name),
        "extension": extension,
        "stem": file_name[: -len(extension)] if extension else file_name,
        "rel_path": rel_path,
        "is_pdf": extension == ".pdf",
    }


def search_files_page(
    cloud_client,
    bucket_name,
    folder_name="",
    search_query="",
    filter_config: Optional[FileFilterConfig] = None,
    continuation_token: Optional[str] = None,
    page_size: int = SEARCH_PROVIDER_PAGE_SIZE,
    max_pages: int = SEARCH_SCAN_PAGES,
):
    """Recursively search a bounded provider window under a folder prefix.

    Search is intentionally recursive: a query must be able to find a file
    inside nested lecture/semester folders, not only direct children. Because
    the provider has no server-side name filter, the scan continues through
    provider pages until the first matches appear (or the page budget is
    reached). Returning early once matches exist keeps the first response fast;
    the continuation token resumes the scan for "load more".

    When the whole budget is scanned without a single match, no continuation
    token is returned so the UI cannot chain unbounded provider scans.

    Returns:
        Tuple of (matching file items, next continuation token)
    """
    if folder_name:
        folder_name = unquote(folder_name)
    if folder_name and not folder_name.endswith("/"):
        folder_name += "/"

    provider_page_size = max(1, min(int(page_size), 1000))
    scan_pages = max(1, min(int(max_pages), 20))
    query = (search_query or "").casefold()
    file_filter = R2FileFilter(filter_config) if filter_config else None
    results = []
    token = continuation_token
    for _ in range(scan_pages):
        params = {
            "Bucket": bucket_name,
            "Prefix": folder_name,
            "MaxKeys": provider_page_size,
        }
        if token:
            params["ContinuationToken"] = token
        response = cloud_client.list_objects_v2(**params)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            if file_extension(key) == ".ts":
                # HLS segments are media plumbing, never search results.
                continue
            item = _file_item(
                key,
                prefix=folder_name,
                size=obj.get("Size", 0),
                last_modified=obj.get("LastModified"),
            )
            if query and query not in item["rel_path"].casefold():
                continue
            if file_filter and not file_filter.should_include_file(item):
                continue
            results.append(item)
            if len(results) >= SEARCH_RESULT_LIMIT:
                break
        token = response.get("NextContinuationToken") if response.get("IsTruncated") else None
        if results or token is None:
            break

    return results, (token if results else None)


def get_r2_client():
    """Return the shared S3-compatible client for the configured R2 account."""
    return boto3.client(
        "s3",
        endpoint_url=getattr(settings, "R2_ENDPOINT_URL", None),
        aws_access_key_id=getattr(settings, "R2_ACCESS_KEY_ID", None),
        aws_secret_access_key=getattr(settings, "R2_SECRET_ACCESS_KEY", None),
        region_name="auto",
    )


def list_current_folder_page(
    cloud_client,
    bucket_name,
    folder_name="",
    filter_config: Optional[FileFilterConfig] = None,
    folders_only=False,
    continuation_token: Optional[str] = None,
    page_size: int = 200,
    search_query: str = "",
):
    """
    List files and folders in R2 storage with optional filtering.
    Reads one bounded provider page. The continuation token is returned to the
    caller so the UI/API can request the next page without materializing a
    whole folder in memory.
    
    The ``folder_name`` param is an R2 key prefix — a slash-delimited path.
    (Previously it used ``-`` as a segment separator, which broke on any
    folder name containing a hyphen.)
    
    Args:
        cloud_client: Boto3 S3 client configured for R2
        bucket_name: Name of the R2 bucket
        folder_name: Slash-delimited folder prefix from the URL
        filter_config: Optional FileFilterConfig for filtering results
        folders_only: If True, only return folders (no files)
        continuation_token: Opaque provider token from the previous page
        page_size: Maximum number of provider entries to inspect
        search_query: When set, search recursively under the prefix (see
            ``search_files_page``) instead of listing one folder level.
    
    Returns:
        Tuple of (contents list, parent_folder path, next continuation token)
    """
    if folder_name:
        folder_name = unquote(folder_name)

    if search_query:
        results, next_token = search_files_page(
            cloud_client,
            bucket_name,
            folder_name,
            search_query,
            filter_config,
            continuation_token=continuation_token,
        )
        parent_folder = None
        if folder_name and '/' in folder_name.rstrip('/'):
            parent_folder = folder_name.rstrip('/').rsplit('/', 1)[0]
        return results, parent_folder, next_token

    parent_folder = None
    if folder_name and '/' in folder_name.rstrip('/'):
        parent_folder = folder_name.rstrip('/').rsplit('/', 1)[0]
    
    # Normalise trailing slash
    if folder_name and not folder_name.endswith("/"):
        folder_name += "/"
    
    page_size = max(1, min(int(page_size), 1000))
    params = {
        "Bucket": bucket_name,
        "Prefix": folder_name,
        "Delimiter": "/",
        "MaxKeys": page_size,
    }
    if continuation_token:
        params["ContinuationToken"] = continuation_token
    response = cloud_client.list_objects_v2(**params)
    files = []
    folders = []
    file_filter = R2FileFilter(filter_config) if filter_config else None
    if "Contents" in response and not folders_only:
        for obj in response["Contents"]:
            key = obj["Key"]
            rel_path = key[len(folder_name):] if folder_name else key
            file_name = key.split("/")[-1]
            if not rel_path or "/" in rel_path.rstrip("/"):
                continue
            file_obj = _file_item(
                key,
                prefix=folder_name,
                size=obj.get("Size", 0),
                last_modified=obj.get("LastModified"),
            )
            if (file_filter and file_filter.should_include_file(file_obj)) or (not file_filter and not file_name.endswith(".ts")):
                files.append(file_obj)

    for folder in response.get("CommonPrefixes", []):
        prefix = folder["Prefix"]
        folder_id = prefix.rstrip("/")
        name = folder_id.rsplit("/", 1)[-1]
        folder_obj = {"id": folder_id, "name": name, "type": "folder"}
        if not file_filter or file_filter.should_include_file(folder_obj):
            folders.append(folder_obj)

    next_token = response.get("NextContinuationToken") if response.get("IsTruncated") else None
    return folders + files, parent_folder, next_token


def list_current_folder(cloud_client, bucket_name, folder_name="", filter_config: Optional[FileFilterConfig] = None, folders_only=False):
    """Preserve the legacy 1,000-entry page contract for form-based callers."""
    contents, parent_folder, _next_token = list_current_folder_page(
        cloud_client, bucket_name, folder_name, filter_config, folders_only, page_size=1000
    )
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

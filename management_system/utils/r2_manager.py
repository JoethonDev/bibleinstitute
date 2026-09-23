"""
R2 Management Utilities

This module provides comprehensive R2 storage management functions including:
- File operations (delete, rename, move, copy)
- Folder management
- Storage analytics
- Smart HLS deletion
"""

import boto3
import os
import posixpath
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from management_system.utils.r2_filters import file_kind


class R2Manager:
    """Comprehensive R2 storage management"""
    
    def __init__(self, client, bucket_name: str, cloudflare_client=None):
        """
        Initialize R2 Manager
        
        Args:
            client: boto3 S3 client configured for R2
            bucket_name: Name of the R2 bucket
            cloudflare_client: Optional CloudflareR2Client for instant usage stats
        """
        self.client = client
        self.bucket_name = bucket_name
        self.cloudflare = cloudflare_client
    
    def delete_file(self, file_key: str) -> bool:
        """
        Delete a single file from R2
        
        Args:
            file_key: Full key/path of the file in R2
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=file_key)
            return True
        except Exception as e:
            print(f"Error deleting file {file_key}: {e}")
            return False
    
    def delete_files_batch(self, file_keys: List[str]) -> Tuple[List[str], List[str]]:
        """
        Delete multiple files in batch
        
        Args:
            file_keys: List of file keys to delete
        
        Returns:
            Tuple of (successful_deletions, failed_deletions)
        """
        successful = []
        failed = []
        
        # AWS S3 delete_objects supports max 1000 objects at once
        batch_size = 1000
        
        for i in range(0, len(file_keys), batch_size):
            batch = file_keys[i:i + batch_size]
            objects_to_delete = [{'Key': key} for key in batch]
            
            try:
                response = self.client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={'Objects': objects_to_delete}
                )
                
                # Check for successful deletions
                if 'Deleted' in response:
                    successful.extend([obj['Key'] for obj in response['Deleted']])
                
                # Check for errors
                if 'Errors' in response:
                    failed.extend([obj['Key'] for obj in response['Errors']])
                    for error in response['Errors']:
                        print(f"Error deleting {error['Key']}: {error['Message']}")
            
            except Exception as e:
                print(f"Error in batch delete: {e}")
                failed.extend(batch)
        
        return successful, failed
    
    def _m3u8_child_keys(self, m3u8_key: str, base_name: str, folder: str) -> List[str]:
        """Resolve the exact `.ts` children referenced by one manifest.

        The canonical layout stores segments under
        ``<folder>/Video Segments/`` and ``<folder>/Audio Segments/`` while the
        playlist carries relative references such as
        ``Video Segments/<base>_000.ts``. Reading the playlist is the source of
        truth so a ``lecture1`` manifest can never collect a sibling
        ``lecture10_*`` object. Legacy same-directory playlists (bare
        ``<base>_NNN.ts`` references) are also accepted.
        """
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=m3u8_key)
            body = response.get("Body")
            try:
                raw = body.read() if body is not None else b""
            finally:
                close = getattr(body, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        pass
        except Exception as e:
            print(f"Error reading manifest {m3u8_key}: {e}")
            return []
        try:
            text = raw.decode("utf-8", errors="strict")
        except Exception as e:
            print(f"Error decoding manifest {m3u8_key}: {e}")
            return []
        # Playlists are small text files; bound the parse to avoid huge reads.
        if len(text) > 2 * 1024 * 1024:
            print(f"Manifest {m3u8_key} too large to parse safely")
            return []
        children: List[str] = []
        seen = set()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.lower().endswith(".ts"):
                continue
            if line.startswith("/") or "\\" in line or "://" in line:
                continue
            if "?" in line or "#" in line:
                continue
            parts = line.split("/")
            if any(not part or part in (".", "..") for part in parts):
                continue
            resolved = posixpath.normpath(posixpath.join(folder, line)) if folder else posixpath.normpath(line)
            if resolved.startswith("../") or resolved in (".", ".."):
                continue
            if folder and not resolved.startswith(folder + "/"):
                continue
            resolved_parts = resolved.split("/")
            if len(resolved_parts) < 2:
                continue
            parent = resolved_parts[-2]
            filename = resolved_parts[-1]
            if parent in ("Video Segments", "Audio Segments"):
                pass
            elif resolved_parts[:-1] == folder.split("/") if folder else "/" not in resolved:
                # Legacy same-directory layout: only exact base matches.
                if not (filename.endswith(".ts") and filename.startswith(base_name + "_")):
                    continue
            else:
                continue
            if resolved != m3u8_key and resolved not in seen:
                seen.add(resolved)
                children.append(resolved)
        return children

    def _m3u8_fallback_scan(self, base_name: str, folder: str) -> List[str]:
        """List canonical segment subfolders for ``base_name`` with pagination.

        Used only when the manifest cannot be read/parsed, so orphaned
        segments are still cleaned without ever guessing outside the two
        canonical subfolders. The ``base + "_"`` prefix rule keeps
        ``lecture1`` from matching ``lecture10_*``.
        """
        found: List[str] = []
        prefixes = []
        if folder:
            prefixes = [folder + "/Video Segments/", folder + "/Audio Segments/"]
        else:
            prefixes = ["Video Segments/", "Audio Segments/"]
        try:
            paginator = self.client.get_paginator("list_objects_v2")
        except Exception as e:
            print(f"Error creating paginator for {base_name}: {e}")
            return []
        try:
            for prefix in prefixes:
                pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
                for page in pages:
                    for obj in page.get("Contents", []):
                        key = obj["Key"]
                        filename = key.split("/")[-1]
                        if filename.endswith(".ts") and filename.startswith(base_name + "_"):
                            # Stay strictly inside the manifest folder subtree.
                            if folder and not key.startswith(folder + "/"):
                                continue
                            if key not in found:
                                found.append(key)
        except Exception as e:
            print(f"Error scanning segments for {base_name}: {e}")
            return []
        return found

    def delete_m3u8_with_segments(self, m3u8_key: str) -> Tuple[List[str], List[str]]:
        """
        Delete an m3u8 manifest and all its related .ts segment files.

        Children are resolved from the playlist itself (canonical
        ``Video Segments/``/``Audio Segments/`` references, plus legacy
        same-directory references) and are deleted BEFORE the parent
        manifest, so a video delete never orphans its segments.

        Args:
            m3u8_key: Full key/path of the .m3u8 file

        Returns:
            Tuple of (successful_deletions, failed_deletions)
        """
        if not m3u8_key.lower().endswith('.m3u8'):
            return [], [m3u8_key]

        # Get the directory and base name of the m3u8 file
        parts = m3u8_key.split('/')[:-1]
        folder = '/'.join(parts)
        base_name = m3u8_key.split('/')[-1][:-len('.m3u8')]

        children = self._m3u8_child_keys(m3u8_key, base_name, folder)
        if not children:
            children = self._m3u8_fallback_scan(base_name, folder)

        successful: List[str] = []
        failed: List[str] = []

        # Delete children first so the parent manifest is removed last.
        if children:
            ok, bad = self.delete_files_batch(children)
            successful.extend(ok)
            failed.extend(bad)

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=m3u8_key)
            successful.append(m3u8_key)
        except Exception as e:
            print(f"Error deleting manifest {m3u8_key}: {e}")
            failed.append(m3u8_key)

        return successful, failed
    
    def rename_file(self, old_key: str, new_key: str) -> bool:
        """
        Rename a file (implemented as copy + delete)
        
        Args:
            old_key: Current file key
            new_key: New file key
        
        Returns:
            bool: True if successful
        """
        try:
            # Copy to new location
            self.client.copy_object(
                Bucket=self.bucket_name,
                CopySource={'Bucket': self.bucket_name, 'Key': old_key},
                Key=new_key
            )
            
            # Delete old file
            self.client.delete_object(Bucket=self.bucket_name, Key=old_key)
            return True
        
        except Exception as e:
            print(f"Error renaming file from {old_key} to {new_key}: {e}")
            return False

    def rename_folder(self, old_prefix: str, new_prefix: str) -> bool:
        """
        Rename a folder by copying all contained objects to a new prefix then deleting the originals.

        Args:
            old_prefix: Current folder path, e.g. "first year/lectures/"
            new_prefix: New folder path,  e.g. "first year/sessions/"

        Returns:
            bool: True if successful
        """
        # Normalise: ensure both prefixes end with "/"
        if not old_prefix.endswith('/'):
            old_prefix += '/'
        if not new_prefix.endswith('/'):
            new_prefix += '/'

        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=old_prefix)

            objects_to_move = []
            for page in pages:
                for obj in page.get('Contents', []):
                    objects_to_move.append(obj['Key'])

            if not objects_to_move:
                # Folder may only be a virtual prefix with no objects — succeed silently
                return True

            # Copy each object to new location
            for old_key in objects_to_move:
                relative = old_key[len(old_prefix):]
                new_key = new_prefix + relative
                self.client.copy_object(
                    Bucket=self.bucket_name,
                    CopySource={'Bucket': self.bucket_name, 'Key': old_key},
                    Key=new_key
                )

            # Batch-delete originals (S3 DeleteObjects supports up to 1000 per call)
            for i in range(0, len(objects_to_move), 1000):
                batch = [{'Key': k} for k in objects_to_move[i:i + 1000]]
                self.client.delete_objects(Bucket=self.bucket_name, Delete={'Objects': batch})

            return True

        except Exception as e:
            print(f"Error renaming folder from {old_prefix} to {new_prefix}: {e}")
            return False

    def move_file(self, file_key: str, destination_folder: str) -> Optional[str]:
        """
        Move a file to a different folder
        
        Args:
            file_key: Current file key
            destination_folder: Destination folder path (should end with /)
        
        Returns:
            New file key if successful, None otherwise
        """
        filename = file_key.split('/')[-1]
        
        if not destination_folder.endswith('/'):
            destination_folder += '/'
        
        new_key = destination_folder + filename
        
        if self.rename_file(file_key, new_key):
            return new_key
        return None
    
    def copy_file(self, source_key: str, destination_key: str) -> bool:
        """
        Copy a file to a new location
        
        Args:
            source_key: Source file key
            destination_key: Destination file key
        
        Returns:
            bool: True if successful
        """
        try:
            self.client.copy_object(
                Bucket=self.bucket_name,
                CopySource={'Bucket': self.bucket_name, 'Key': source_key},
                Key=destination_key
            )
            return True
        except Exception as e:
            print(f"Error copying file from {source_key} to {destination_key}: {e}")
            return False
    
    def create_folder(self, folder_path: str) -> bool:
        """
        Create a folder (by creating an empty object with / suffix)
        
        Args:
            folder_path: Path of the folder to create
        
        Returns:
            bool: True if successful
        """
        if not folder_path.endswith('/'):
            folder_path += '/'
        
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=folder_path,
                Body=b''
            )
            return True
        except Exception as e:
            print(f"Error creating folder {folder_path}: {e}")
            return False
    
    def delete_folder(self, folder_path: str, recursive: bool = False) -> Tuple[int, int]:
        """
        Delete a folder
        
        Args:
            folder_path: Path of the folder to delete
            recursive: If True, delete all contents; if False, only delete if empty
        
        Returns:
            Tuple of (files_deleted, errors)
        """
        if not folder_path.endswith('/'):
            folder_path += '/'
        
        # List all objects in the folder
        objects_to_delete = []
        
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=folder_path)
            
            for page in pages:
                if 'Contents' in page:
                    objects_to_delete.extend([obj['Key'] for obj in page['Contents']])
            
            if not recursive and len(objects_to_delete) > 1:
                # Folder is not empty and recursive is False
                return 0, 1
            
            if objects_to_delete:
                successful, failed = self.delete_files_batch(objects_to_delete)
                return len(successful), len(failed)
            
            return 0, 0
        
        except Exception as e:
            print(f"Error deleting folder {folder_path}: {e}")
            return 0, 1
    
    def get_file_metadata(self, file_key: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a file
        
        Args:
            file_key: File key in R2
        
        Returns:
            Dictionary with file metadata or None if error
        """
        try:
            response = self.client.head_object(Bucket=self.bucket_name, Key=file_key)
            
            return {
                'key': file_key,
                'name': file_key.split('/')[-1],
                'size': response.get('ContentLength', 0),
                'last_modified': response.get('LastModified'),
                'content_type': response.get('ContentType', 'unknown'),
                'etag': response.get('ETag', '').strip('"'),
            }
        except Exception as e:
            print(f"Error getting metadata for {file_key}: {e}")
            return None
    
    def get_storage_stats(self, prefix: str = "") -> Dict[str, Any]:
        """
        Get storage statistics for a folder or entire bucket.
        
        Uses the Cloudflare native usage API when a ``cloudflare_client`` is
        configured – instant, no pagination.  Falls back to boto3 pagination
        (up to 20 pages) when Cloudflare is not available.
        
        Args:
            prefix: Folder prefix to analyze (empty for entire bucket)
        
        Returns:
            Dictionary with storage statistics
        """
        stats = {
            'total_files': 0,
            'total_size': 0,
            'by_extension': {},
            'by_kind': {},
            'largest_files': [],
            'approximate': False,
        }
        
        # Fast path: Cloudflare native usage API for exact totals
        usage = self.cloudflare.get_bucket_usage(self.bucket_name)
        if usage:
            stats['total_files'] = int(usage.get('objectCount', 0))
            stats['total_size'] = int(usage.get('payloadSize', 0))
            # Still sample one page via boto3 for extension/kind breakdown
            try:
                sample = self.client.list_objects_v2(
                    Bucket=self.bucket_name, MaxKeys=1000
                )
                if 'Contents' in sample:
                    for obj in sample['Contents']:
                        key = obj['Key']
                        if key.endswith('/'):
                            continue
                        if '.' in key:
                            ext = key.split('.')[-1].lower()
                            if ext not in stats['by_extension']:
                                stats['by_extension'][ext] = {'count': 0, 'size': 0}
                            stats['by_extension'][ext]['count'] += 1
                            stats['by_extension'][ext]['size'] += obj.get('Size', 0)
                        kind = file_kind(key)
                        if kind not in stats['by_kind']:
                            stats['by_kind'][kind] = {'count': 0, 'size': 0}
                        stats['by_kind'][kind]['count'] += 1
                        stats['by_kind'][kind]['size'] += obj.get('Size', 0)
            except Exception:
                pass
        
        
        return stats
    
    def format_file_size(self, bytes_size: int) -> str:
        """
        Format file size to human-readable format
        
        Args:
            bytes_size: Size in bytes
        
        Returns:
            Formatted string (e.g., "1.5 MB")
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} PB"

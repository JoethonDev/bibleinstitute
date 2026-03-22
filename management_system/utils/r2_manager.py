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
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime


class R2Manager:
    """Comprehensive R2 storage management"""
    
    def __init__(self, client, bucket_name: str):
        """
        Initialize R2 Manager
        
        Args:
            client: boto3 S3 client configured for R2
            bucket_name: Name of the R2 bucket
        """
        self.client = client
        self.bucket_name = bucket_name
    
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
    
    def delete_m3u8_with_segments(self, m3u8_key: str) -> Tuple[List[str], List[str]]:
        """
        Delete an m3u8 file and all its related .ts segment files
        Only deletes .ts files in the SAME directory as the m3u8 file
        
        Args:
            m3u8_key: Full key/path of the .m3u8 file
        
        Returns:
            Tuple of (successful_deletions, failed_deletions)
        """
        if not m3u8_key.endswith('.m3u8'):
            return [], [m3u8_key]
        
        # Get the directory and base name of the m3u8 file
        directory = '/'.join(m3u8_key.split('/')[:-1])
        if directory:
            directory += '/'
        
        base_name = m3u8_key.split('/')[-1].replace('.m3u8', '')
        
        files_to_delete = [m3u8_key]
        
        # List all files in the same directory
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=directory
            )
            
            if 'Contents' in response:
                for obj in response['Contents']:
                    key = obj['Key']
                    filename = key.split('/')[-1]
                    
                    # Only include .ts files that match the pattern and are in the same directory
                    if (filename.endswith('.ts') and 
                        filename.startswith(base_name) and
                        '/' not in key[len(directory):]):
                        files_to_delete.append(key)
        
        except Exception as e:
            print(f"Error listing segments for {m3u8_key}: {e}")
            return [], [m3u8_key]
        
        # Delete all files
        return self.delete_files_batch(files_to_delete)
    
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
        Get storage statistics for a folder or entire bucket
        
        Args:
            prefix: Folder prefix to analyze (empty for entire bucket)
        
        Returns:
            Dictionary with storage statistics
        """
        stats = {
            'total_files': 0,
            'total_size': 0,
            'by_extension': {},
            'largest_files': [],
        }
        
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
            
            all_files = []
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        size = obj.get('Size', 0)
                        
                        # Skip folders (keys ending with /)
                        if key.endswith('/'):
                            continue
                        
                        stats['total_files'] += 1
                        stats['total_size'] += size
                        
                        # Track by extension
                        if '.' in key:
                            ext = key.split('.')[-1].lower()
                            if ext not in stats['by_extension']:
                                stats['by_extension'][ext] = {
                                    'count': 0,
                                    'size': 0
                                }
                            stats['by_extension'][ext]['count'] += 1
                            stats['by_extension'][ext]['size'] += size
                        
                        # Track for largest files
                        all_files.append({
                            'key': key,
                            'size': size,
                            'last_modified': obj.get('LastModified')
                        })
            
            # Get top 10 largest files
            all_files.sort(key=lambda x: x['size'], reverse=True)
            stats['largest_files'] = all_files[:10]
        
        except Exception as e:
            print(f"Error getting storage stats: {e}")
        
        return stats
    
    def search_files(self, query: str, prefix: str = "", extensions: List[str] = None) -> List[Dict[str, Any]]:
        """
        Search for files by name
        
        Args:
            query: Search query (case-insensitive substring match)
            prefix: Folder prefix to search in
            extensions: Optional list of extensions to filter by
        
        Returns:
            List of matching files
        """
        results = []
        query_lower = query.lower()
        
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        filename = key.split('/')[-1].lower()
                        
                        # Skip folders
                        if key.endswith('/'):
                            continue
                        
                        # Check if query matches filename
                        if query_lower not in filename:
                            continue
                        
                        # Check extension if specified
                        if extensions:
                            if not any(key.lower().endswith(f'.{ext.lower()}') for ext in extensions):
                                continue
                        
                        results.append({
                            'key': key,
                            'name': key.split('/')[-1],
                            'size': obj.get('Size', 0),
                            'last_modified': obj.get('LastModified'),
                        })
        
        except Exception as e:
            print(f"Error searching files: {e}")
        
        return results
    
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

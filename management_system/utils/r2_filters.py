"""
R2 File Filtering Utilities

This module provides filtering utilities for Cloudflare R2 file listings
to optimize performance by filtering files based on extension and other criteria.
"""

from typing import List, Set, Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class FileFilterConfig:
    """Configuration for file filtering"""
    allowed_extensions: Set[str] = field(default_factory=set)
    exclude_extensions: Set[str] = field(default_factory=set)
    include_folders: bool = True
    case_sensitive: bool = False
    include_hidden: bool = False
    max_size: Optional[int] = None  # in bytes
    min_size: Optional[int] = None  # in bytes

    def __post_init__(self):
        """Normalize extensions to lowercase if case insensitive"""
        if not self.case_sensitive:
            self.allowed_extensions = {ext.lower() for ext in self.allowed_extensions}
            self.exclude_extensions = {ext.lower() for ext in self.exclude_extensions}


class R2FileFilter:
    """Filter R2 file listings based on criteria"""
    
    def __init__(self, config: FileFilterConfig):
        self.config = config
    
    def should_include_file(self, file_obj: Dict[str, Any]) -> bool:
        """
        Determine if a file should be included based on filter config
        
        Args:
            file_obj: Dictionary containing file information with keys:
                     - name: filename
                     - type: 'file' or 'folder'
                     - size: file size in bytes (optional)
        
        Returns:
            bool: True if file should be included, False otherwise
        """
        file_name = file_obj.get('name', '')
        file_type = file_obj.get('type', 'file')
        file_size = file_obj.get('size', 0)
        
        # Always include folders if configured
        if file_type == 'folder':
            return self.config.include_folders
        
        # Check hidden files
        if not self.config.include_hidden and file_name.startswith('.'):
            return False
        
        # Check file size constraints
        if self.config.max_size is not None and file_size > self.config.max_size:
            return False
        if self.config.min_size is not None and file_size < self.config.min_size:
            return False
        
        # Get file extension
        extension = self._get_extension(file_name)
        
        # Check exclusions first
        if extension in self.config.exclude_extensions:
            return False
        
        # If allowed_extensions is empty, allow all (except excluded)
        if not self.config.allowed_extensions:
            return True
        
        # Check if extension is in allowed list
        return extension in self.config.allowed_extensions
    
    def _get_extension(self, filename: str) -> str:
        """Extract file extension from filename"""
        if '.' not in filename:
            return ''
        
        extension = filename.rsplit('.', 1)[-1]
        
        if not self.config.case_sensitive:
            extension = extension.lower()
        
        return extension
    
    def filter_files(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter a list of files based on the configuration
        
        Args:
            files: List of file dictionaries
        
        Returns:
            Filtered list of files
        """
        return [f for f in files if self.should_include_file(f)]
    
    def get_file_stats(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Get statistics about filtered files
        
        Args:
            files: List of file dictionaries (already filtered)
        
        Returns:
            Dictionary with statistics
        """
        stats = {
            'total_files': 0,
            'total_folders': 0,
            'total_size': 0,
            'by_extension': {},
        }
        
        for file_obj in files:
            if file_obj.get('type') == 'folder':
                stats['total_folders'] += 1
            else:
                stats['total_files'] += 1
                file_size = file_obj.get('size', 0)
                stats['total_size'] += file_size
                
                # Count by extension
                extension = self._get_extension(file_obj.get('name', ''))
                if extension:
                    if extension not in stats['by_extension']:
                        stats['by_extension'][extension] = {
                            'count': 0,
                            'size': 0
                        }
                    stats['by_extension'][extension]['count'] += 1
                    stats['by_extension'][extension]['size'] += file_size
        
        return stats


# Preset filter configurations
FILTER_PRESETS = {
    'media': FileFilterConfig(
        allowed_extensions={'mp4', 'mp3', 'm3u8', 'pdf'},
        exclude_extensions={'ts'},  # Exclude HLS segments
        include_folders=True,
    ),
    'video': FileFilterConfig(
        allowed_extensions={'mp4', 'm3u8'},
        exclude_extensions={'ts'},
        include_folders=True,
    ),
    'audio': FileFilterConfig(
        allowed_extensions={'mp3', 'wav', 'ogg', 'm4a'},
        include_folders=True,
    ),
    'documents': FileFilterConfig(
        allowed_extensions={'pdf', 'doc', 'docx', 'txt'},
        include_folders=True,
    ),
    'all': FileFilterConfig(
        allowed_extensions=set(),  # Empty means allow all
        exclude_extensions={'ts'},  # Still exclude HLS segments
        include_folders=True,
    ),
}


def get_filter_preset(preset_name: str) -> FileFilterConfig:
    """
    Get a preset filter configuration
    
    Args:
        preset_name: Name of the preset ('media', 'video', 'audio', 'documents', 'all')
    
    Returns:
        FileFilterConfig object
    """
    return FILTER_PRESETS.get(preset_name, FILTER_PRESETS['media'])


def create_custom_filter(allowed: List[str] = None, 
                        excluded: List[str] = None,
                        **kwargs) -> FileFilterConfig:
    """
    Create a custom filter configuration
    
    Args:
        allowed: List of allowed extensions
        excluded: List of excluded extensions
        **kwargs: Additional configuration options
    
    Returns:
        FileFilterConfig object
    """
    return FileFilterConfig(
        allowed_extensions=set(allowed) if allowed else set(),
        exclude_extensions=set(excluded) if excluded else set(),
        **kwargs
    )

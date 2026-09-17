"""
R2 File Filtering Utilities

This module provides the canonical file-kind classification and filtering
utilities for Cloudflare R2 listings so the drive page, its search, its stats,
and the file-card template all agree on what a file is.

Canonical kinds (matched by name and extension, never by a second rule set):

- ``video``: HLS manifest (``.m3u8``) or a video extension whose name does not
  contain "audio";
- ``audio``: any file whose name contains "audio" (for example the paired
  ``_audio.m3u8`` manifest and ``_audio.mp3`` download) or an audio extension;
- ``image``: common image extensions;
- ``document``: PDFs (plus legacy office/text documents);
- ``other``: everything else, including HLS ``.ts`` segments.
"""

from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set

from dataclasses import dataclass, field
from django.utils.translation import gettext_lazy as _

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif", ".heic", ".tif", ".tiff",
})
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".mpeg", ".mpg",
})
AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac",
})
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"})
MANIFEST_EXTENSION = ".m3u8"

KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_IMAGE = "image"
KIND_DOCUMENT = "document"
KIND_OTHER = "other"


def file_extension(file_name: str) -> str:
    """Return the lowercase extension including the leading dot."""
    if not isinstance(file_name, str) or not file_name:
        return ""
    return PurePosixPath(file_name).suffix.lower()


def file_kind(file_name: str) -> str:
    """Classify one file name into the canonical R2 kind.

    The "audio" name marker wins over the video/manifest extension so an
    ``..._audio.m3u8`` file is audio, while a plain ``..._0001.m3u8`` is video.
    """
    if not isinstance(file_name, str) or not file_name:
        return KIND_OTHER
    name = file_name.casefold()
    extension = file_extension(file_name)
    if extension == ".pdf":
        return KIND_DOCUMENT
    if extension in IMAGE_EXTENSIONS:
        return KIND_IMAGE
    if extension == MANIFEST_EXTENSION or extension in VIDEO_EXTENSIONS:
        return KIND_AUDIO if "audio" in name else KIND_VIDEO
    if extension in AUDIO_EXTENSIONS:
        return KIND_AUDIO
    if extension in DOCUMENT_EXTENSIONS:
        return KIND_DOCUMENT
    return KIND_OTHER


@dataclass
class FileFilterConfig:
    """Configuration for file filtering"""
    allowed_extensions: Set[str] = field(default_factory=set)
    exclude_extensions: Set[str] = field(default_factory=set)
    kinds: Optional[Set[str]] = None
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
        if self.kinds is not None:
            self.kinds = {kind.lower() for kind in self.kinds}


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

        # Kind presets use the canonical classifier only.
        if self.config.kinds is not None:
            return file_kind(file_name) in self.config.kinds

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


# Canonical filter presets — one kind-based rule set shared by the drive page,
# its search, and its stats.
FILTER_PRESETS = {
    'media': FileFilterConfig(
        kinds={KIND_VIDEO, KIND_AUDIO, KIND_IMAGE},
        include_folders=True,
    ),
    'video': FileFilterConfig(
        kinds={KIND_VIDEO},
        include_folders=True,
    ),
    'audio': FileFilterConfig(
        kinds={KIND_AUDIO},
        include_folders=True,
    ),
    'document': FileFilterConfig(
        kinds={KIND_DOCUMENT},
        include_folders=True,
    ),
    'all': FileFilterConfig(
        allowed_extensions=set(),  # Empty means allow all
        exclude_extensions={'ts'},  # Still exclude HLS segments
        include_folders=True,
    ),
}

FILTER_PRESET_LABELS = {
    'media': _("Media"),
    'video': _("Video"),
    'audio': _("Audio"),
    'document': _("Documents"),
    'all': _("All files"),
}


def filter_preset_choices() -> List[Dict[str, str]]:
    """Ordered ``value``/``label`` options for the drive filter select."""
    return [
        {"value": preset, "label": str(FILTER_PRESET_LABELS[preset])}
        for preset in FILTER_PRESETS
    ]


def get_filter_preset(preset_name: str) -> FileFilterConfig:
    """
    Get a preset filter configuration
    
    Args:
        preset_name: Name of the preset ('media', 'video', 'audio', 'document', 'all')
    
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

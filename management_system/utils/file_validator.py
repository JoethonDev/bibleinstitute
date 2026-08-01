"""
File Validation Utility for Upload Restrictions
Supports MP4 (video), MP3 (audio), and PDF (document) validation
"""
import os
import mimetypes
from typing import Tuple, List, Optional
from django.utils.translation import gettext as _


# Allowed file types configuration
ALLOWED_EXTENSIONS = {'.mp4', '.mp3', '.pdf'}
ALLOWED_MIME_TYPES = {
    'video/mp4',
    'audio/mp3',
    'audio/mpeg',
    'application/pdf'
}
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB in bytes


class FileValidator:
    """Utility class for comprehensive file validation"""
    
    @staticmethod
    def get_file_extension(filename: str) -> str:
        """
        Extract file extension from filename
        
        Args:
            filename: Name of the file
            
        Returns:
            File extension in lowercase (including the dot)
        """
        return os.path.splitext(filename)[1].lower()
    
    @staticmethod
    def is_allowed_extension(filename: str) -> bool:
        """
        Check if file extension is allowed
        
        Args:
            filename: Name of the file
            
        Returns:
            True if extension is allowed, False otherwise
        """
        ext = FileValidator.get_file_extension(filename)
        return ext in ALLOWED_EXTENSIONS
    
    @staticmethod
    def is_allowed_mime_type(mime_type: str) -> bool:
        """
        Check if MIME type is allowed
        
        Args:
            mime_type: MIME type string
            
        Returns:
            True if MIME type is allowed, False otherwise
        """
        return mime_type in ALLOWED_MIME_TYPES
    
    @staticmethod
    def get_mime_type_from_extension(filename: str) -> Optional[str]:
        """
        Get MIME type from file extension
        
        Args:
            filename: Name of the file
            
        Returns:
            MIME type string or None if not found
        """
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type
    
    @staticmethod
    def get_file_type_label(filename: str) -> str:
        """
        Get human-readable file type label
        
        Args:
            filename: Name of the file
            
        Returns:
            File type label (e.g., "MP4 Video", "MP3 Audio", "PDF Document")
        """
        ext = FileValidator.get_file_extension(filename)
        labels = {
            '.mp4': _('MP4 Video'),
            '.mp3': _('MP3 Audio'),
            '.pdf': _('PDF Document')
        }
        return labels.get(ext, _('Unknown'))
    
    @staticmethod
    def validate_file_size(size: int) -> Tuple[bool, Optional[str]]:
        """
        Validate file size
        
        Args:
            size: File size in bytes
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if size > MAX_FILE_SIZE:
            max_size_mb = MAX_FILE_SIZE / (1024 * 1024)
            return False, _('File size exceeds maximum allowed size of %(max_size)sMB') % {'max_size': max_size_mb}
        return True, None
    
    @staticmethod
    def validate_filename(filename: str) -> Tuple[bool, List[str]]:
        """
        Validate filename for allowed extensions
        
        Args:
            filename: Name of the file to validate
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        if not filename:
            errors.append(_('Filename cannot be empty'))
            return False, errors
        
        # Check extension
        if not FileValidator.is_allowed_extension(filename):
            ext = FileValidator.get_file_extension(filename) or _('(no extension)')
            errors.append(
                _('File type "%(ext)s" is not allowed. Allowed types: MP4, MP3, PDF') % {'ext': ext}
            )
        
        # Check MIME type
        mime_type = FileValidator.get_mime_type_from_extension(filename)
        if mime_type and not FileValidator.is_allowed_mime_type(mime_type):
            errors.append(
                _('MIME type "%(mime)s" is not allowed') % {'mime': mime_type}
            )
        
        is_valid = len(errors) == 0
        return is_valid, errors


def validate_upload_filename(filename: str, size: Optional[int] = None) -> Tuple[bool, List[str]]:
    """
    Comprehensive validation function for uploaded files
    
    Args:
        filename: Name of the file to validate
        size: Optional file size in bytes
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Validate filename and extension
    is_filename_valid, filename_errors = FileValidator.validate_filename(filename)
    errors.extend(filename_errors)
    
    # Validate file size if provided
    if size is not None:
        is_size_valid, size_error = FileValidator.validate_file_size(size)
        if not is_size_valid:
            errors.append(size_error)
    
    is_valid = len(errors) == 0
    return is_valid, errors


def get_file_category(filename: str) -> str:
    """
    Get file category based on extension
    
    Args:
        filename: Name of the file
        
    Returns:
        Category string: 'video', 'audio', or 'document'
    """
    ext = FileValidator.get_file_extension(filename)
    categories = {
        '.mp4': 'video',
        '.mp3': 'audio',
        '.pdf': 'document'
    }
    return categories.get(ext, 'unknown')


def validate_hls_object_key(key: str) -> Tuple[bool, List[str]]:
    """
    Validate an HLS object key (manifest .m3u8 or segment .ts) for R2 storage.

    Valid manifest: a safe R2 path ending in .m3u8 whose parent path does NOT
    contain 'Video Segments' or 'Audio Segments' as a path component.
    A root-level manifest (e.g. "lecture.m3u8") is valid.

    Valid segment: a safe R2 path ending in .ts whose immediate parent
    component is exactly 'Video Segments' or 'Audio Segments'.

    Rejects absolute paths, backslashes, empty components, '.'/'..' components,
    control characters, query/fragment characters, keys longer than 1024
    characters, unsupported extensions, and segment keys without the required
    media-folder structure.

    Returns:
        Tuple of (is_valid, list_of_translated_error_messages)
    """
    errors: List[str] = []

    if not key:
        errors.append(_("HLS object key cannot be empty."))
        return False, errors

    if len(key) > 1024:
        errors.append(_("HLS object key is too long (maximum 1024 characters)."))
        return False, errors

    if key.startswith("/"):
        errors.append(_("HLS object key must not be an absolute path."))
        return False, errors

    if "\\" in key:
        errors.append(_("HLS object key must not contain backslashes."))
        return False, errors

    if "?" in key or "#" in key:
        errors.append(
            _("HLS object key must not contain query or fragment characters.")
        )
        return False, errors

    for ch in key:
        if ord(ch) < 32 or ord(ch) == 127:
            errors.append(
                _("HLS object key must not contain control characters.")
            )
            return False, errors

    parts = key.split("/")

    if any(not part for part in parts):
        errors.append(
            _("HLS object key must not contain empty path components.")
        )
        return False, errors

    if any(part in {".", ".."} for part in parts):
        errors.append(
            _('HLS object key must not contain "." or ".." path components.')
        )
        return False, errors

    filename = parts[-1]

    if not (filename.endswith(".m3u8") or filename.endswith(".ts")):
        ext = os.path.splitext(filename)[1] or _("(no extension)")
        errors.append(
            _('File type "%(ext)s" is not allowed. Allowed HLS types: .m3u8, .ts')
            % {"ext": ext}
        )
        return False, errors

    if filename.endswith(".m3u8"):
        # Manifest check: no Video Segments or Audio Segments in parent path
        parent_parts = parts[:-1]
        for component in parent_parts:
            if component == "Video Segments" or component == "Audio Segments":
                errors.append(
                    _(
                        'HLS manifest key must not contain "Video Segments" '
                        'or "Audio Segments" in its path.'
                    )
                )
                return False, errors
        return True, []

    # Segment check (ends with .ts)
    if len(parts) < 2:
        errors.append(
            _(
                'HLS segment key must have a "Video Segments" or '
                '"Audio Segments" media folder.'
            )
        )
        return False, errors

    parent_folder = parts[-2]
    if parent_folder not in ("Video Segments", "Audio Segments"):
        errors.append(
            _(
                'HLS segment key must have "Video Segments" or '
                '"Audio Segments" as the immediate parent folder.'
            )
        )
        return False, errors

    return True, []

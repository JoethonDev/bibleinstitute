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

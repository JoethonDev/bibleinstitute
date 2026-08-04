"""
Google Drive Storage Manager (Legacy Code)
This module contains the original Google Drive implementation.
Preserved for potential future use or migration back to Google Drive.

Note: This code is currently not in use. The system uses R2 storage.
To switch back to Google Drive:
1. Uncomment the imports in views.py
2. Import GoogleDriveManager instead of R2Manager
3. Update the STORAGE_MANAGER initialization
4. Install required dependencies: pip install google-api-python-client
"""
import io
from logging import getLogger

logger = getLogger(__name__)

# Google Drive API dependencies (install with: pip install google-api-python-client)
try:
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
except ImportError:
    MediaIoBaseDownload = None
    MediaIoBaseUpload = None
    logger.warning("Google Drive API client not installed. Google Drive functionality will not work.")


class GoogleDriveManager:
    """
    Manager class for Google Drive storage operations.
    Provides similar interface to R2Manager for easy switching.
    """
    
    def __init__(self, drive_client):
        """
        Initialize Google Drive Manager.
        
        Args:
            drive_client: Authenticated Google Drive API client
        """
        self.drive_client = drive_client
        logger.info("GoogleDriveManager initialized")
    
    def list_folder(self, folder_id=None, root=True):
        """
        List files and folders in a Google Drive folder.
        
        Args:
            folder_id: Google Drive folder ID (None for root/shared)
            root: Boolean indicating if listing shared with me items
        
        Returns:
            List of dictionaries containing file/folder info, and parent_id
        """
        query = (
            f"'{folder_id}' in parents and trashed=false" 
            if not root 
            else "sharedWithMe=true and trashed=false"
        )
        
        try:
            results = self.drive_client.files().list(
                q=query,
                fields="files(id, name, mimeType)"
            ).execute()
            
            storage_data = results.get("files", [])
            drive = []
            
            for data in storage_data:
                file_type = (
                    "folder" 
                    if data['mimeType'] == "application/vnd.google-apps.folder" 
                    else "file"
                )
                
                drive.append({
                    "id": data['id'],
                    "name": data['name'],
                    "type": file_type,
                })
            
            # Get parent folder info for navigation
            parent_id = ""
            if not root:
                folder_details = self.drive_client.files().get(
                    fileId=folder_id,
                    fields="id, name, parents"
                ).execute()
                
                # Check if the queried folder has a parent
                if "parents" in folder_details:
                    parent_id = folder_details["parents"][0]
                
                return drive, parent_id
            
            return drive, None
            
        except Exception as e:
            logger.error(f"Error listing Google Drive folder: {e}")
            return [], None
    
    def download_file(self, file_id):
        """
        Download a file from Google Drive.
        
        Args:
            file_id: Google Drive file ID
        
        Returns:
            BytesIO object containing file data
        """
        try:
            if MediaIoBaseDownload is None:
                logger.error("Google Drive API client not installed")
                return None
            
            # Get file request
            file_request = self.drive_client.files().get_media(fileId=file_id)
            
            in_memory = io.BytesIO()
            downloader = MediaIoBaseDownload(in_memory, file_request)
            
            done = False
            while not done:
                try:
                    _, done = downloader.next_chunk()
                except Exception as chunk_error:
                    logger.error(f"Error downloading chunk: {chunk_error}")
                    break
            
            in_memory.seek(0)
            return in_memory
            
        except Exception as e:
            logger.error(f"Error downloading file from Google Drive: {e}")
            return None
    
    def get_file_metadata(self, file_id):
        """
        Get metadata for a file.
        
        Args:
            file_id: Google Drive file ID
        
        Returns:
            Dictionary containing file metadata
        """
        try:
            file_metadata = self.drive_client.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, createdTime, modifiedTime"
            ).execute()
            
            return file_metadata
            
        except Exception as e:
            logger.error(f"Error getting file metadata: {e}")
            return None
    
    def upload_file(self, file_name, file_data, folder_id=None, mime_type=None):
        """
        Upload a file to Google Drive.
        
        Args:
            file_name: Name of the file
            file_data: File data (BytesIO or file-like object)
            folder_id: Parent folder ID (optional)
            mime_type: MIME type of the file (optional)
        
        Returns:
            Dictionary with file ID and name, or None on error
        """
        try:
            if MediaIoBaseUpload is None:
                logger.error("Google Drive API client not installed")
                return None
            
            file_metadata = {'name': file_name}
            
            if folder_id:
                file_metadata['parents'] = [folder_id]
            
            media = MediaIoBaseUpload(
                file_data,
                mimetype=mime_type or 'application/octet-stream',
                resumable=True
            )
            
            file = self.drive_client.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name'
            ).execute()
            
            logger.info(f"File uploaded to Google Drive: {file_name} (ID: {file['id']})")
            return file
            
        except Exception as e:
            logger.error(f"Error uploading file to Google Drive: {e}")
            return None
    
    def delete_file(self, file_id):
        """
        Delete a file from Google Drive.
        
        Args:
            file_id: Google Drive file ID
        
        Returns:
            Boolean indicating success
        """
        try:
            self.drive_client.files().delete(fileId=file_id).execute()
            logger.info(f"File deleted from Google Drive: {file_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting file from Google Drive: {e}")
            return False
    
    def create_folder(self, folder_name, parent_folder_id=None):
        """
        Create a new folder in Google Drive.
        
        Args:
            folder_name: Name of the new folder
            parent_folder_id: Parent folder ID (optional)
        
        Returns:
            Dictionary with folder ID and name, or None on error
        """
        try:
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            
            if parent_folder_id:
                file_metadata['parents'] = [parent_folder_id]
            
            folder = self.drive_client.files().create(
                body=file_metadata,
                fields='id, name'
            ).execute()
            
            logger.info(f"Folder created in Google Drive: {folder_name} (ID: {folder['id']})")
            return folder
            
        except Exception as e:
            logger.error(f"Error creating folder in Google Drive: {e}")
            return None


def get_drive_client():
    """
    Initialize and return Google Drive API client.
    This function should be implemented based on your authentication setup.
    
    Returns:
        Authenticated Google Drive API client
    """
    # NOTE: This is a placeholder. Implement actual Google Drive authentication.
    # Example implementation might use service account or OAuth2 credentials.
    
    # from google.oauth2 import service_account
    # from googleapiclient.discovery import build
    
    # SCOPES = ['https://www.googleapis.com/auth/drive']
    # SERVICE_ACCOUNT_FILE = 'path/to/service-account-key.json'
    
    # credentials = service_account.Credentials.from_service_account_file(
    #     SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    
    # service = build('drive', 'v3', credentials=credentials)
    # return service
    
    raise NotImplementedError(
        "Google Drive client initialization not implemented. "
        "Uncomment and configure the authentication code above."
    )

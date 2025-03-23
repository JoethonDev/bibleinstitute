from django.dispatch import receiver
from django.db.models.signals import pre_save
from management_system.models import Lesson
# from management_system.utils import get_drive_client
from logging import Logger

# Third Party Libraries
import io
# from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import json
import re

# DRIVE = get_drive_client()
# logger = Logger(__name__)

# def get_m3u8_file(file_id):
#     file = DRIVE.files().get_media(fileId=file_id)
#     memory_file = io.BytesIO()
#     downloader = MediaIoBaseDownload(memory_file, file)
#     done = False
#     while not done:
#         status, done = downloader.next_chunk()
#         print(f"Download {int(status.progress() * 100)}% complete.")
#     memory_file.seek(0)
#     return memory_file

# def extract_segments(file_content):
#     segments_names = re.findall(r"^.*\.ts$", file_content, re.MULTILINE)
#     query = ' or '.join([f"name = '{name}'" for name in segments_names])
#     results = DRIVE.files().list(q=query, fields="files(id, name)", pageSize=1000).execute()
#     items = results.get('files', [])
#     return items

# def modify_content(content: str, items: dict):
#     for item in items:
#         segment_name = "{{path}}/" + item['id']
#         content = content.replace(item['name'], segment_name.strip("\n"))
#     return content

# def upload_file(file, file_id):
#     media = MediaFileUpload(file)

#     updated_file = DRIVE.files().update(
#         fileId=file_id,
#         media_body=media
#     ).execute() 
    

# @receiver(pre_save, sender=Lesson)
# def modify_m3u8(sender, instance, **kwargs):
#     links = json.loads(instance.links)

#     if instance.pk is not None:
#         logger.info(f"Lesson {instance.pk} is being updated!")
#         previous_links = json.loads(
#             Lesson.objects.get(pk=instance.pk).links
#         )
#         if set(json.dumps(data) for data in links) == set(json.dumps(data) for data in previous_links):
#             logger.info(f"Lesson {instance.pk} has no difference in recorded videos")
#             return  
        
#     modified_links = []
#     for m3u8_file in links:
#         file_id = m3u8_file['id']
#         if not m3u8_file['name'].endswith(".m3u8"):
#             logger.error(f"Can not process file : {m3u8_file['name']}, because it is not m3u8 file")
#             continue
#         memory_file = get_m3u8_file(file_id)
#         logger.info(f"File : {file_id} is retrieved from google drive!")

#         content = memory_file.read().decode("utf-8")
#         if "{{path}}" in content:
#             logger.info(f"File : {file_id} does not need modifications")
#             continue
#         segments = extract_segments(content)
#         content = modify_content(content, segments)
#         logger.info(f"File : {file_id} has updated its content")


#         with open('modified_file.m3u8', 'w') as f:
#             f.write(content)

#         upload_file('modified_file.m3u8', file_id)
#         logger.info(f"File : {file_id} has uploaded successfully to google drive!")
#         m3u8_file['segments'] = [item['id'] for item in segments]
#         modified_links.append(m3u8_file)
    
#     instance.links = json.dumps(modified_links)
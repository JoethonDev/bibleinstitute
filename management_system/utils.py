# from google.oauth2 import service_account
# from googleapiclient.discovery import build
# from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
# import os
# import re
# import io
# import time
# import boto3

# def get_drive_client():
#     path = os.getcwd() + "\\management_system"

#     # Load credentials from the file
#     creds = service_account.Credentials.from_service_account_file(
#         f'{path}\\cred.json', scopes=['https://www.googleapis.com/auth/drive']
#     )

#     # Build the Drive service
#     service = build('drive', 'v3', credentials=creds)

#     return service


# def parse_range_header(range_header):
#     range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
#     if range_match:
#         start = int(range_match.group(1))
#         end = range_match.group(2)
#         end = int(end) if end else None
#         return start, end
#     return 0, 8152



# client = get_drive_client()

# """Retrieve file information including its parent folder."""
# # file_info = client.files().get(fileId="1B4dmto5Jrdrl4RZ7FFmQ0ARm_rc1Sn9f", fields="name, parents").execute()
# file_id = "1LJ9ogFBKgYA8QnWT5Jtprj7FrdpP1VYf"
# whole_process = time.time()
# start_time = time.time()
# file_info = client.files().get_media(fileId=file_id)
# print(f"Time to get file : {(time.time() - start_time)}")
# memory_file = io.BytesIO()
# downloader = MediaIoBaseDownload(memory_file, file_info, 2560)
# # Select Video Path Link => Encoding into segments => Save HLS Path

# # Download the file into memory
# start_time = time.time()
# done = False
# while not done:
#     status, done = downloader.next_chunk()
#     print(f"Download {int(status.progress() * 100)}% complete.")
# print(f"Time to download file : {(time.time() - start_time)}")


# memory_file.seek(0)

# content = memory_file.read().decode("utf-8")
# segments_names = re.findall(r"/.*\r", content)
# names = ",".join(segments_names)
# names = names.replace("\r", "").replace("/", "").split(",")
# print(names)

# query = ' or '.join([f"name = '{name}'" for name in segments_names])

# # start_time = time.time()
# results = client.files().list(q=query, fields="files(id, name)").execute()
# items = results.get('files', [])

# for item in items:
#     print(item['id'])

# print(f"Time to query files : {(time.time() - start_time)}")

# if items:
#     start_time = time.time()
#     for item in items:
#         path = "{{path}}/" + item['id']
#         content = content.replace(item['name'], path)
#     print(f"Time to edit files : {(time.time() - start_time)}")

#     print(content)

#     with open('modified_file.m3u8', 'w') as f:
#         f.write(content)

#     media = MediaFileUpload('modified_file.m3u8')

#     start_time = time.time()
#     updated_file = client.files().update(
#         fileId=file_id,
#         media_body=media
#     ).execute() 
#     print(f"Time to upload file : {(time.time() - start_time)}")

# print(f"Time to finish task : {(time.time() - whole_process)}")

# print(f"Updated File ID: {updated_file.get('id')}")
# print(f"Content : {content}")


# print(segments_files)

# CLOUD_CLIENT = boto3.client(
#     's3',
#     endpoint_url='https://da59dca47179969defd66c61b710bbdb.r2.cloudflarestorage.com',
#     aws_access_key_id='34aab6f5a4a4e832bf2619260e0dbaea',
#     aws_secret_access_key='ac0690c0799ff35373e92d8ee6c1d6a986798e6578de992fe39965ea90df038e',
#     region_name='auto'
# )

# bucket_name = 'bible-institute'
# folder_name = ""
# objects = CLOUD_CLIENT.list_objects_v2(Bucket=bucket_name, Prefix=folder_name, Delimiter="/")

# # print(objects)
# # Files
# # if "Contents" in objects:
# #     for obj in objects["Contents"]:
# #         print("Object Key:", obj["Key"])

# # print(objects["Contents"])
# # # Folders
# # if "CommonPrefixes" in objects:
# #     for folder in objects["CommonPrefixes"]:
# #         print("Object Key:", folder["Prefix"])

# objects = CLOUD_CLIENT.list_objects_v2(Bucket=bucket_name, Prefix="new_lecture/", Delimiter="/")
# print(objects)
# if "CommonPrefixes" in objects:
#     for folder in objects["CommonPrefixes"]:
#         print("Object Key:", folder["Prefix"])

# # print(objects["CommonPrefixes"])
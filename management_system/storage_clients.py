"""Shared provider clients for the web and mobile storage adapters."""

import boto3
from django.conf import settings

from .utils.cloudflare_provider import CloudflareR2Client


CLOUD_CLIENT = boto3.client(
    "s3",
    endpoint_url=getattr(settings, "R2_ENDPOINT_URL", None),
    aws_access_key_id=getattr(settings, "R2_ACCESS_KEY_ID", None),
    aws_secret_access_key=getattr(settings, "R2_SECRET_ACCESS_KEY", None),
    region_name="auto",
)
bucket_name = getattr(settings, "R2_BUCKET_NAME", "")

cf_account_id = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
cf_api_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
cloudflare_client = (
    CloudflareR2Client(cf_account_id, cf_api_token)
    if cf_account_id and cf_api_token
    else None
)

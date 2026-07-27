import requests
from typing import Optional, Dict, Any
from logging import getLogger

logger = getLogger(__name__)


class CloudflareR2Client:
    """Native Cloudflare REST API client for R2 operations.

    Uses the Cloudflare API token (not S3 access keys) to call the
    un-paginated usage endpoint for instant bucket size and object count.
    """

    def __init__(self, account_id: str, api_token: str):
        self.account_id = account_id
        self.api_token = api_token
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}"

    def get_bucket_usage(self, bucket_name: str) -> Optional[Dict[str, Any]]:
        """Fetch instant bucket usage stats (object count + total size).

        GET …/r2/buckets/{bucket_name}/usage

        Returns dict with keys: payloadSize, metadataSize, objectCount
        or None on failure.
        """
        url = f"{self.base_url}/r2/buckets/{bucket_name}/usage"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            body = resp.json()
            if body.get("success"):
                return body["result"]
            logger.error("Cloudflare API error: %s", body.get("errors", body))
        except requests.RequestException as e:
            logger.error("Cloudflare API request failed: %s", e)

        return None

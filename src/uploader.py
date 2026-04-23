import logging
from datetime import datetime, timezone

import requests

from config import Config

logger = logging.getLogger(__name__)


def upload_image(
    image_data: bytes,
    config: Config,
    filename: str,
    captured_at: str = "",
) -> bool:
    """Upload a JPEG image via HTTPS POST with multipart/form-data.

    Args:
        image_data: Raw JPEG bytes.
        config: Application configuration.
        filename: Original filename for the image.
        captured_at: ISO 8601 UTC capture timestamp. Falls back to now() if empty.

    Returns:
        True on success, False on failure.
    """
    if not captured_at:
        captured_at = datetime.now(timezone.utc).isoformat()

    files = {
        "image": (filename, image_data, "image/jpeg"),
    }
    data = {
        "device_id": config.device_id,
        "captured_at": captured_at,
        "filename": filename,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
    }

    try:
        response = requests.post(
            config.upload_url,
            files=files,
            data=data,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        logger.info("Upload succeeded: %s (status %d)", filename, response.status_code)
        return True

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if 400 <= status < 500 and status not in (408, 429):
            logger.critical(
                "Permanent HTTP error uploading %s: status %d — "
                "check API_KEY and UPLOAD_URL (will not retry)",
                filename,
                status,
            )
        else:
            logger.error("HTTP error uploading %s: status %d", filename, status)
    except requests.exceptions.ConnectionError as exc:
        logger.error("Connection error uploading %s: %s", filename, exc)
    except requests.exceptions.Timeout:
        logger.error("Timeout uploading %s (30s exceeded)", filename)
    except requests.exceptions.RequestException as exc:
        logger.error("Unexpected request error uploading %s: %s", filename, exc)

    return False

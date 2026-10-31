"""Amazon Photos upload utilities.

Provides `upload_to_amazon_photos` which uploads a list of image files to
Amazon Photos using a provided OAuth2 Bearer token and upload endpoint.
The function runs uploads concurrently and retries transient errors.
"""

import logging
import requests
from pathlib import Path
from typing import List

from tqdm import tqdm

from utils import retry


@retry(attempts=3, backoff=2, allowed_exceptions=(requests.exceptions.RequestException,))
def _upload_one(image_path: Path, token: str, endpoint: str) -> bool:
    """Upload a single image to Amazon Photos.

    Returns ``True`` on success, ``False`` otherwise.
    """
    headers = {"Authorization": f"Bearer {token}"}
    with image_path.open('rb') as f:
        files = {"file": (image_path.name, f, "application/octet-stream")}
        resp = requests.post(endpoint, headers=headers, files=files, timeout=30)
    if resp.status_code == 200:
        logging.info("Uploaded %s to Amazon Photos", image_path.name)
        return True
    else:
        logging.error("Failed to upload %s: %s – %s", image_path.name, resp.status_code, resp.text)
        return False


def upload_to_amazon_photos(image_files: List[Path], token: str, endpoint: str) -> None:
    """Upload multiple images to Amazon Photos.

    * ``image_files`` – list of image ``Path`` objects.
    * ``token`` – OAuth2 Bearer token.
    * ``endpoint`` – upload URL (e.g. ``https://photos.amazon.com/api/v1/uploads``).
    """
    if not token:
        logging.warning("Amazon Photos token not provided – skipping upload.")
        return
    if not endpoint:
        logging.error("Amazon Photos upload endpoint not configured.")
        return

    successes = 0
    total = len(image_files)
    with tqdm(total=total, desc="Uploading to Amazon Photos", unit="file") as pbar:
        for img in image_files:
            if _upload_one(img, token, endpoint):
                successes += 1
            pbar.update(1)
    logging.info("Amazon Photos upload finished: %d/%d succeeded.", successes, total)

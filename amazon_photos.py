"""Amazon Photos upload utilities.

Provides `upload_to_amazon_photos` which either:
1. Copies/moves images to a local directory monitored by the official
   Amazon Photos desktop app (when ``sync_dir`` is provided), or
2. Uploads a list of image files to an HTTP endpoint using a provided
   OAuth2 Bearer token.
"""

import logging
import shutil
import requests
from pathlib import Path
from typing import List, Optional

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


def upload_to_amazon_photos(
    image_files: List[Path],
    token: str = "",
    endpoint: str = "",
    sync_dir: Optional[str] = None,
) -> None:
    """Upload or stage multiple images for Amazon Photos.

    * ``image_files`` – list of image ``Path`` objects.
    * ``token`` – OAuth2 Bearer token (for HTTP upload mode).
    * ``endpoint`` – upload URL (for HTTP upload mode).
    * ``sync_dir`` – local directory path monitored by the Amazon Photos
      desktop app. When set, images are copied to this folder instead of
      using the HTTP endpoint.
    """
    if sync_dir:
        target_dir = Path(sync_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        total = len(image_files)
        copied = 0
        with tqdm(total=total, desc="Staging for Amazon Photos", unit="file") as pbar:
            for img in image_files:
                dest = target_dir / img.name
                shutil.copy2(img, dest)
                copied += 1
                pbar.update(1)
        logging.info(
            "Staged %d/%d images into Amazon Photos sync folder: %s",
            copied,
            total,
            target_dir,
        )
        return

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

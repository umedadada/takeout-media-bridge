"""Downloader module for Takeout archive.

Provides `download_takeout` which fetches the archive using a retry decorator
to handle transient 503 errors.
"""

import logging
import requests
from pathlib import Path
from tqdm import tqdm

from utils import retry


@retry(attempts=5, backoff=5, allowed_exceptions=(requests.exceptions.RequestException,))
def _http_get(url: str):
    """Internal helper that performs the GET request and raises for HTTP errors."""
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    return resp


def download_takeout(url: str, dest_dir: Path) -> Path:
    """Download the Google Takeout archive with retry on transient errors.

    Returns the path to the downloaded file.
    """
    logging.info("Downloading Takeout archive from %s", url)
    response = _http_get(url)
    # Determine filename from Content‑Disposition or fallback
    filename = response.headers.get("content-disposition")
    if filename and "filename=" in filename:
        filename = filename.split("filename=")[1].strip('"')
    else:
        filename = "takeout_archive.zip"
    archive_path = dest_dir / filename
    total_size = int(response.headers.get("content-length", 0))
    with archive_path.open("wb") as f:
        with tqdm(total=total_size, desc=f"Downloading {filename}", unit="B", unit_scale=True) as pbar:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))
    logging.info("Archive saved to %s", archive_path)
    return archive_path

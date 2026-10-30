"""R2 client utilities.

Provides functions to obtain a bucket resource, upload files (with retry),
upload a list of files concurrently, and check object existence.
"""

import logging
import concurrent.futures
from pathlib import Path
from typing import List, Tuple

import boto3
from boto3.s3.transfer import TransferConfig
from tqdm import tqdm

from utils import retry


# TransferConfig for multipart uploads – files > 8 MB use multipart,
# each part is 8 MB. This enables resumable uploads for large files.
_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=8 * 1024 * 1024,   # 8 MB
    multipart_chunksize=8 * 1024 * 1024,   # 8 MB parts
    max_concurrency=4,
    use_threads=True,
)


def get_r2_client(r2_cfg: dict):
    """Create a boto3 bucket resource from the R2 configuration dict."""
    session = boto3.session.Session()
    s3 = session.resource(
        service_name="s3",
        endpoint_url=r2_cfg["endpoint_url"],
        aws_access_key_id=r2_cfg["access_key_id"],
        aws_secret_access_key=r2_cfg["secret_access_key"],
    )
    return s3.Bucket(r2_cfg["bucket"])


@retry(attempts=3, backoff=2)
def upload_to_r2(bucket, file_path: Path, key_prefix: str = "") -> str:
    """Upload a single file to R2 and return the object key."""
    key = f"{key_prefix}{file_path.name}" if not key_prefix.endswith('/') else f"{key_prefix}{file_path.name}"
    logging.debug("Uploading %s to R2 as %s", file_path, key)
    bucket.upload_file(str(file_path), key, Config=_TRANSFER_CONFIG)
    return key


def upload_all_to_r2(r2_cfg: dict, files: List[Path]) -> List[Tuple[Path, str]]:
    """Upload all files concurrently, returning a list of (local_path, r2_key)."""
    bucket = get_r2_client(r2_cfg)
    uploaded: List[Tuple[Path, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_path = {executor.submit(upload_to_r2, bucket, f, key_prefix="takeout/"): f for f in files}
        with tqdm(total=len(files), desc="Uploading to R2", unit="file") as pbar:
            for future in concurrent.futures.as_completed(future_to_path):
                f = future_to_path[future]
                try:
                    key = future.result()
                    uploaded.append((f, key))
                except Exception as exc:
                    logging.error("Failed to upload %s: %s", f, exc)
                finally:
                    pbar.update(1)
    logging.info("Uploaded %d files to R2 (concurrent)", len(uploaded))
    return uploaded


def object_exists(bucket, key: str) -> bool:
    """Return True if the object key exists in the bucket (lightweight HEAD)."""
    try:
        bucket.Object(key).load()
        return True
    except Exception:
        return False

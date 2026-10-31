"""R2 client utilities.

Provides functions to obtain a bucket resource, upload files (with retry),
upload a list of files concurrently, list/download objects from R2, and check
object existence.
"""

import logging
import concurrent.futures
from pathlib import Path
from typing import List, Optional, Tuple

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
def upload_to_r2(
    bucket,
    file_path: Path,
    key_prefix: str = "",
    base_dir: Optional[Path] = None,
) -> str:
    """Upload a single file to R2 and return the object key."""
    if base_dir is not None:
        try:
            rel_path = file_path.relative_to(base_dir).as_posix()
        except ValueError:
            rel_path = file_path.name
    else:
        rel_path = file_path.name

    prefix = key_prefix if not key_prefix or key_prefix.endswith("/") else f"{key_prefix}/"
    key = f"{prefix}{rel_path}"
    logging.debug("Uploading %s to R2 as %s", file_path, key)
    bucket.upload_file(str(file_path), key, Config=_TRANSFER_CONFIG)
    return key


def upload_all_to_r2(
    r2_cfg: dict,
    files: List[Path],
    base_dir: Optional[Path] = None,
    key_prefix: str = "takeout/",
) -> List[Tuple[Path, str]]:
    """Upload all files concurrently, returning a list of (local_path, r2_key)."""
    bucket = get_r2_client(r2_cfg)
    uploaded: List[Tuple[Path, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_path = {
            executor.submit(
                upload_to_r2, bucket, f, key_prefix=key_prefix, base_dir=base_dir
            ): f
            for f in files
        }
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


def list_r2_keys(r2_cfg: dict, prefix: str = "takeout/") -> List[str]:
    """List all object keys in R2 under the given prefix."""
    bucket = get_r2_client(r2_cfg)
    keys = [
        obj.key
        for obj in bucket.objects.filter(Prefix=prefix)
        if not obj.key.endswith("/")
    ]
    logging.info("Found %d objects in R2 under prefix '%s'", len(keys), prefix)
    return keys


@retry(attempts=3, backoff=2)
def download_from_r2(
    bucket, key: str, dest_dir: Path, prefix: str = "takeout/"
) -> Path:
    """Download a single object from R2 into *dest_dir* preserving relative structure."""
    rel_key = key[len(prefix) :] if prefix and key.startswith(prefix) else Path(key).name
    local_path = dest_dir / rel_key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    logging.debug("Downloading %s from R2 to %s", key, local_path)
    bucket.download_file(key, str(local_path), Config=_TRANSFER_CONFIG)
    return local_path


def download_all_from_r2(
    r2_cfg: dict, dest_dir: Path, prefix: str = "takeout/"
) -> List[Path]:
    """Download all objects under *prefix* from R2 concurrently."""
    bucket = get_r2_client(r2_cfg)
    keys = list_r2_keys(r2_cfg, prefix=prefix)
    downloaded: List[Path] = []
    if not keys:
        return downloaded

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_key = {
            executor.submit(download_from_r2, bucket, k, dest_dir, prefix): k
            for k in keys
        }
        with tqdm(total=len(keys), desc="Downloading from R2", unit="file") as pbar:
            for future in concurrent.futures.as_completed(future_to_key):
                k = future_to_key[future]
                try:
                    path = future.result()
                    downloaded.append(path)
                except Exception as exc:
                    logging.error("Failed to download %s from R2: %s", k, exc)
                finally:
                    pbar.update(1)
    logging.info("Downloaded %d files from R2", len(downloaded))
    return downloaded


def object_exists(bucket, key: str) -> bool:
    """Return True if the object key exists in the bucket (lightweight HEAD)."""
    try:
        bucket.Object(key).load()
        return True
    except Exception:
        return False

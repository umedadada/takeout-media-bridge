"""Verifier module for strict upload verification.

Provides `verify_upload_strict` which checks that each uploaded file exists in the
R2 bucket and that its MD5 checksum (or multipart ETag) matches the local file.
Runs concurrently with a tqdm progress bar.
"""

import concurrent.futures
import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from tqdm import tqdm

MULTIPART_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB (matches r2_client._TRANSFER_CONFIG)


def _compute_s3_etag(local_path: Path, part_number_count: int = 0) -> str:
    """Compute the expected S3/R2 ETag for *local_path*."""
    if part_number_count > 0:
        part_md5s = []
        with local_path.open("rb") as f:
            while True:
                chunk = f.read(MULTIPART_CHUNK_SIZE)
                if not chunk:
                    break
                part_md5s.append(hashlib.md5(chunk).digest())
        combined = b"".join(part_md5s)
        return f"{hashlib.md5(combined).hexdigest()}-{len(part_md5s)}"

    md5_hash = hashlib.md5()
    with local_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def _verify_one(bucket, local_path: Path, key: str) -> Optional[Tuple[Path, str, str]]:
    """Verify a single uploaded file. Returns None if OK, or (local_path, key, reason)."""
    obj = bucket.Object(key)
    try:
        obj.load()
    except Exception as e:
        return (local_path, key, f"Object missing or error: {e}")

    etag = obj.e_tag.strip('"')
    parts = 0
    if "-" in etag:
        try:
            parts = int(etag.split("-")[1])
        except ValueError:
            parts = 0

    expected_etag = _compute_s3_etag(local_path, part_number_count=parts)
    if etag != expected_etag:
        return (
            local_path,
            key,
            f"ETag/MD5 mismatch (local={expected_etag}, remote={etag})",
        )
    return None


def verify_upload_strict(
    bucket, uploads: List[Tuple[Path, str]]
) -> Tuple[bool, List[Tuple[Path, str, str]]]:
    """Strict verification of uploaded files (concurrent with progress bar)."""
    mismatches: List[Tuple[Path, str, str]] = []
    if not uploads:
        return True, mismatches

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [
            executor.submit(_verify_one, bucket, local_path, key)
            for local_path, key in uploads
        ]
        with tqdm(total=len(uploads), desc="Verifying in R2", unit="file") as pbar:
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res is not None:
                    mismatches.append(res)
                pbar.update(1)

    all_ok = len(mismatches) == 0
    return all_ok, mismatches

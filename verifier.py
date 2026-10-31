"""Verifier module for strict upload verification.

Provides `verify_upload_strict` which checks that each uploaded file exists in the
R2 bucket and that its MD5 checksum (or multipart ETag) matches the local file.
"""

import hashlib
import logging
from pathlib import Path
from typing import List, Tuple

MULTIPART_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB (matches r2_client._TRANSFER_CONFIG)


def _compute_s3_etag(local_path: Path, part_number_count: int = 0) -> str:
    """Compute the expected S3/R2 ETag for *local_path*.

    If *part_number_count* > 0 (i.e. remote ETag is of the form ``<hex>-<N>``),
    computes the multipart ETag using 8 MB chunks. Otherwise computes standard MD5.
    """
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


def verify_upload_strict(
    bucket, uploads: List[Tuple[Path, str]]
) -> Tuple[bool, List[Tuple[Path, str, str]]]:
    """Strict verification of uploaded files.

    * ``bucket`` – a boto3 Bucket object.
    * ``uploads`` – list of ``(local_path, r2_key)`` tuples.

    Returns ``(all_ok, mismatches)`` where ``mismatches`` contains tuples
    ``(local_path, r2_key, reason)`` for any missing or checksum‑mismatched
    files.
    """
    mismatches: List[Tuple[Path, str, str]] = []
    for local_path, key in uploads:
        obj = bucket.Object(key)
        try:
            obj.load()
        except Exception as e:
            mismatches.append((local_path, key, f"Object missing or error: {e}"))
            continue

        etag = obj.e_tag.strip('"')
        parts = 0
        if "-" in etag:
            try:
                parts = int(etag.split("-")[1])
            except ValueError:
                parts = 0

        expected_etag = _compute_s3_etag(local_path, part_number_count=parts)
        if etag != expected_etag:
            mismatches.append(
                (local_path, key, f"ETag/MD5 mismatch (local={expected_etag}, remote={etag})")
            )
    all_ok = len(mismatches) == 0
    return all_ok, mismatches

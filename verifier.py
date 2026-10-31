"""Verifier module for strict upload verification.

Provides `verify_upload_strict` which checks that each uploaded file exists in the
R2 bucket and that its MD5 checksum matches the local file.
"""

import hashlib
import logging
from pathlib import Path
from typing import List, Tuple


def verify_upload_strict(bucket, uploads: List[Tuple[Path, str]]) -> Tuple[bool, List[Tuple[Path, str, str]]]:
    """Strict verification of uploaded files.

    * ``bucket`` – a boto3 Bucket object.
    * ``uploads`` – list of ``(local_path, r2_key)`` tuples.

    Returns ``(all_ok, mismatches)`` where ``mismatches`` contains tuples
    ``(local_path, r2_key, reason)`` for any missing or checksum‑mismatched
    files.
    """
    mismatches: List[Tuple[Path, str, str]] = []
    for local_path, key in uploads:
        # Compute local MD5
        md5_hash = hashlib.md5()
        with local_path.open('rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5_hash.update(chunk)
        local_md5 = md5_hash.hexdigest()
        # Retrieve remote object's ETag
        obj = bucket.Object(key)
        try:
            obj.load()
        except Exception as e:
            mismatches.append((local_path, key, f"Object missing or error: {e}"))
            continue
        # ETag may be quoted
        etag = obj.e_tag.strip('"')
        if etag != local_md5:
            mismatches.append((local_path, key, f"MD5 mismatch (local={local_md5}, remote={etag})"))
    all_ok = len(mismatches) == 0
    return all_ok, mismatches

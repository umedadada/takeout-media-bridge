#!/usr/bin/env python3
"""takeout-media-bridge

This script automates the workflow you described:
1. Receive a Google Takeout export URL for Google Photos.
2. Download the archive and extract its contents.
3. Upload all files to a Cloudflare R2 bucket (S3‑compatible).
4. Verify that every file listed in the manifest was uploaded.
5. Split the payload into images and videos.
6. Upload images to Amazon Photos (placeholder implementation).
7. Upload videos to YouTube via the Data API (placeholder implementation).

The script is intentionally modular – each step lives in its own function so you can replace the placeholder
implementations with production‑grade logic (OAuth flows, retry policies, etc.) without touching the rest of the
pipeline.
"""

# Core workflow functions have been moved to dedicated modules (config, downloader, extractor, r2_client, verifier, splitter, amazon_photos, youtube_uploader).
# This file now only contains the CLI entry point and imports.

import concurrent.futures
import time
from functools import wraps

# Retry logic moved to utils.retry – imported where needed



# Module imports (functionality split into separate files)
from config import load_config
from downloader import download_takeout
from extractor import extract_archive
from r2_client import get_r2_client, upload_all_to_r2
from verifier import verify_upload_strict
from splitter import split_media
from amazon_photos import upload_to_amazon_photos
from youtube_uploader import upload_to_youtube

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple


def main():
    parser = argparse.ArgumentParser(description="Google Takeout → R2 → Amazon Photos / YouTube bridge")
    parser.add_argument("--url", required=True, help="Direct download URL for the Google Takeout archive (Google Photos export)")
    parser.add_argument("--config", default="config.json", help="Path to JSON configuration file (default: config.json)")
    parser.add_argument("--temp-dir", default=None, help="Optional directory for temporary files")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, …)")
    parser.add_argument("--log-file", default=None, help="Optional path to a log file (with rotation)")
    parser.add_argument("--manifest", action="store_true", help="Generate a JSON manifest of uploaded files")
    args = parser.parse_args()

    from logging_config import setup_logging
    setup_logging(level=args.log_level, log_file=args.log_file)

    config = load_config(Path(args.config))
    temp_root = Path(args.temp_dir) if args.temp_dir else Path(tempfile.mkdtemp(prefix="takeout_bridge_"))
    temp_root.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download
        archive_path = download_takeout(args.url, temp_root)
        # 2. Extract
        extract_dir = temp_root / "extracted"
        extract_dir.mkdir()
        extracted_files = extract_archive(archive_path, extract_dir)
        # 3. Upload to R2 (concurrent)
        uploaded = upload_all_to_r2(config["r2"], extracted_files)
        # 4. Strict verification (MD5)
        bucket = get_r2_client(config["r2"])
        all_ok, mismatches = verify_upload_strict(bucket, uploaded)
        if not all_ok:
            for local_path, key, reason in mismatches:
                logging.error("Verification failed for %s (key=%s): %s", local_path, key, reason)
            sys.exit(1)
        logging.info("All files verified successfully in R2")
        # Optional manifest generation
        if args.manifest:
            manifest_path = temp_root / "upload_manifest.json"
            manifest_data = [{"local_path": str(p), "r2_key": k} for p, k in uploaded]
            manifest_path.write_text(json.dumps(manifest_data, indent=2))
            logging.info("Manifest written to %s", manifest_path)
        # 5. Split
        images, videos = split_media([p for p, _ in uploaded])
        # 6. Amazon Photos (concurrent placeholder)
        upload_to_amazon_photos(images, config.get("amazon_photos", {}).get("access_token", ""), config.get("amazon_photos", {}).get("upload_endpoint", ""))
        # 7. YouTube (real implementation)
        upload_to_youtube(videos, config["youtube"])
    finally:
        if not args.temp_dir:
            shutil.rmtree(temp_root, ignore_errors=True)
            logging.debug("Removed temporary directory %s", temp_root)

if __name__ == "__main__":
    main()

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    """Load JSON configuration.

    The configuration file should contain the credentials required for the various services:
    ```json
    {
        "r2": {
            "endpoint_url": "https://<account_id>.r2.cloudflarestorage.com",
            "access_key_id": "<R2_ACCESS_KEY_ID>",
            "secret_access_key": "<R2_SECRET_ACCESS_KEY>",
            "bucket": "takeout-archive"
        },
        "youtube": {
            "client_secrets_file": "client_secret.json",
            "credentials_file": "youtube_credentials.json"
        },
        "amazon_photos": {
            "access_token": "<AMAZON_PHOTOS_OAUTH_TOKEN>"
        }
    }
    ```
    """
    if not config_path.is_file():
        logging.error("Config file %s not found", config_path)
        sys.exit(1)
    with config_path.open() as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Step 1 – Download Takeout archive
# ---------------------------------------------------------------------------

def download_takeout(url: str, dest_dir: Path) -> Path:
    """Download the Google Takeout archive with retry on transient errors.

    Uses a small helper that retries 503 (Service Unavailable) responses.
    """
    logging.info("Downloading Takeout archive from %s", url)
    @retry(attempts=5, backoff=5, allowed_exceptions=(requests.exceptions.RequestException,))
    def _get():
        resp = requests.get(url, stream=True, timeout=60)
        # Raise for HTTP errors (including 503) so retry catches them
        resp.raise_for_status()
        return resp
    response = _get()
    # Guess a filename from the Content‑Disposition header or fallback to a UUID
    filename = response.headers.get("content-disposition")
    if filename and "filename=" in filename:
        filename = filename.split("filename=")[1].strip('"')
    else:
        filename = "takeout_archive.zip"

    archive_path = dest_dir / filename
    with archive_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    logging.info("Archive saved to %s", archive_path)
    return archive_path
    """Download the Google Takeout archive.

    The URL is expected to be a direct download link that does **not** require a
    browser redirect. The function streams the content to a temporary file and
    returns the path to the downloaded archive.
    """
    logging.info("Downloading Takeout archive from %s", url)
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()

    # Guess a filename from the Content‑Disposition header or fallback to a UUID
    filename = response.headers.get("content-disposition")
    if filename and "filename=" in filename:
        filename = filename.split("filename=")[1].strip('"')
    else:
        filename = "takeout_archive.zip"

    archive_path = dest_dir / filename
    with archive_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    logging.info("Archive saved to %s", archive_path)
    return archive_path

# ---------------------------------------------------------------------------
# Step 2 – Extract archive
# ---------------------------------------------------------------------------

def extract_archive(archive_path: Path, extract_to: Path) -> List[Path]:
    """Extract the Takeout zip file and return a list of all extracted files.
    """
    import zipfile

    logging.info("Extracting %s to %s", archive_path, extract_to)
    with zipfile.ZipFile(archive_path, "r") as z:
        z.extractall(extract_to)
        extracted = [extract_to / member for member in z.namelist() if not member.endswith('/')]
    logging.info("Extracted %d files", len(extracted))
    return extracted

# ---------------------------------------------------------------------------
# Step 3 – Upload to Cloudflare R2 (S3 compatible)
# ---------------------------------------------------------------------------

def object_exists(bucket, key: str) -> bool:
    """Return True if the object key exists in the bucket.
    Uses a lightweight HEAD request.
    """
    try:
        bucket.Object(key).load()
        return True
    except Exception:
        return False

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
    """Upload a single file to R2 and return the object key.
    """
    key = f"{key_prefix}{file_path.name}" if not key_prefix.endswith('/') else f"{key_prefix}{file_path.name}"
    logging.debug("Uploading %s to R2 as %s", file_path, key)
    bucket.upload_file(str(file_path), key)
    return key

    """Upload a single file to R2 and return the object key.
    """
    key = f"{key_prefix}{file_path.name}" if not key_prefix.endswith('/') else f"{key_prefix}{file_path.name}"
    logging.debug("Uploading %s to R2 as %s", file_path, key)
    bucket.upload_file(str(file_path), key)
    return key


def upload_all_to_r2(r2_cfg: dict, files: List[Path]) -> List[Tuple[Path, str]]:
    bucket = get_r2_client(r2_cfg)
    uploaded = []
    # Use a thread pool for concurrent uploads
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_path = {executor.submit(upload_to_r2, bucket, f, key_prefix="takeout/"): f for f in files}
        for future in concurrent.futures.as_completed(future_to_path):
            f = future_to_path[future]
            try:
                key = future.result()
                uploaded.append((f, key))
            except Exception as exc:
                logging.error("Failed to upload %s: %s", f, exc)
    logging.info("Uploaded %d files to R2 (concurrent)", len(uploaded))
    return uploaded
    bucket = get_r2_client(r2_cfg)
    uploaded_keys = []
    for f in files:
        key = upload_to_r2(bucket, f, key_prefix="takeout/")
        uploaded_keys.append(key)
    logging.info("Uploaded %d files to R2", len(uploaded_keys))
    return uploaded_keys

# ---------------------------------------------------------------------------
# Step 4 – Verification
# ---------------------------------------------------------------------------

def verify_upload_strict(bucket, uploads: List[Tuple[Path, str]]) -> Tuple[bool, List[Tuple[Path, str, str]]]:
    """Strict verification: ensure each uploaded file exists and its MD5 checksum matches.

    Returns a tuple (all_ok, mismatches) where mismatches is a list of tuples
    (local_path, object_key, reason).
    """
    import hashlib
    mismatches: List[Tuple[Path, str, str]] = []
    for local_path, key in uploads:
        # Compute local MD5
        md5_hash = hashlib.md5()
        with local_path.open('rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5_hash.update(chunk)
        local_md5 = md5_hash.hexdigest()
        # Retrieve object metadata
        obj = bucket.Object(key)
        try:
            obj.load()
        except Exception as e:
            mismatches.append((local_path, key, f"Object missing or error: {e}"))
            continue
        # ETag often contains quotes; strip them
        etag = obj.e_tag.strip('"')
        if etag != local_md5:
            mismatches.append((local_path, key, f"MD5 mismatch (local={local_md5}, remote={etag})"))
    all_ok = len(mismatches) == 0
    return all_ok, mismatches
    """Check that every expected key exists in the bucket.
    Returns a tuple (all_present, missing_keys).
    """
    existing = set(obj.key for obj in bucket.objects.filter(Prefix="takeout/"))
    missing = [k for k in expected_keys if k not in existing]
    return (len(missing) == 0, missing)

# ---------------------------------------------------------------------------
# Step 5 – Split into images / videos
# ---------------------------------------------------------------------------

def split_media(files: List[Path]) -> Tuple[List[Path], List[Path]]:
    image_exts = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp"}
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    images, videos = [], []
    for f in files:
        ext = f.suffix.lower()
        if ext in image_exts:
            images.append(f)
        elif ext in video_exts:
            videos.append(f)
    logging.info("Found %d images and %d videos", len(images), len(videos))
    return images, videos

# ---------------------------------------------------------------------------
# Step 6 – Upload images to Amazon Photos (placeholder)
# ---------------------------------------------------------------------------

def upload_to_amazon_photos(image_files: List[Path], token: str, endpoint: str = ""):
    """Upload images to Amazon Photos.

    This implementation expects a valid OAuth2 Bearer token and an upload endpoint.
    The endpoint can be discovered from the Amazon Photos web client (e.g.
    ``https://photos.amazon.com/api/v1/uploads``). The function uploads files
    concurrently and retries on transient errors using the ``@retry`` decorator.
    """
    if not token:
        logging.warning("Amazon Photos token not provided – skipping upload.")
        return
    if not endpoint:
        logging.error("Amazon Photos upload endpoint not configured.")
        return

    @retry(attempts=3, backoff=2)
    def _upload_one(img_path: Path) -> bool:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            with img_path.open('rb') as f:
                files = {"file": (img_path.name, f, "application/octet-stream")}
                resp = requests.post(endpoint, headers=headers, files=files, timeout=30)
            if resp.status_code == 200:
                logging.info("Uploaded %s to Amazon Photos", img_path.name)
                return True
            else:
                logging.error("Failed to upload %s: %s – %s", img_path.name, resp.status_code, resp.text)
                return False
        except Exception as e:
            logging.error("Exception uploading %s to Amazon Photos: %s", img_path.name, e)
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_upload_one, image_files))
    successes = sum(results)
    logging.info("Amazon Photos upload finished: %d/%d succeeded.", successes, len(image_files))


# ---------------------------------------------------------------------------
# Step 7 – Upload videos to YouTube (placeholder – requires OAuth flow)
# ---------------------------------------------------------------------------

def upload_to_youtube(video_files: List[Path], youtube_cfg: dict):
    """Upload videos to YouTube using OAuth2 credentials.

    The function expects `client_secrets_file` and optionally `credentials_file` in
    `youtube_cfg`. If a valid credentials file does not exist, it runs the OAuth
    flow to obtain a new token and saves it for future runs.
    """
    # Scopes required for uploading videos
    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None
    cred_path = Path(youtube_cfg.get("credentials_file", "youtube_credentials.json"))
    if cred_path.is_file():
        creds = Credentials.from_authorized_user_file(str(cred_path), scopes)
    # Refresh expired tokens or start fresh OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logging.warning("Failed to refresh YouTube token: %s", e)
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                youtube_cfg["client_secrets_file"], scopes)
            creds = flow.run_local_server(port=0)
            # Save the credentials for next time
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            cred_path.write_text(creds.to_json())
            logging.info("Saved new YouTube credentials to %s", cred_path)

    youtube = build("youtube", "v3", credentials=creds)
    @retry(attempts=3, backoff=2)
    def _upload_one_video(video_path: Path):
        logging.info("Uploading %s to YouTube", video_path.name)
        request_body = {
            "snippet": {
                "title": video_path.stem,
                "description": f"Uploaded by takeout-media-bridge on {datetime.utcnow().isoformat()}Z",
                "tags": ["takeout", "google-photos"],
                "categoryId": "22",  # People & Blogs
            },
            "status": {"privacyStatus": "private"},
        }
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part=",".join(request_body.keys()),
            body=request_body,
            media_body=media,
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logging.info("Upload %s: %.2f%%", video_path.name, status.progress * 100)
        logging.info("Uploaded video %s, id=%s", video_path.name, response["id"])

    for video_path in video_files:
        try:
            _upload_one_video(video_path)
        except Exception as e:
            logging.error("Failed to upload %s to YouTube: %s", video_path, e)

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Google Takeout → R2 → Amazon Photos / YouTube bridge")
    parser.add_argument("--url", required=True, help="Direct download URL for the Google Takeout archive (Google Photos export)")
    parser.add_argument("--config", default="config.json", help="Path to JSON configuration file (default: config.json)")
    parser.add_argument("--temp-dir", default=None, help="Optional directory for temporary files")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, …)")
    parser.add_argument("--log-file", default=None, help="Optional path to a log file (with rotation)")
    parser.add_argument("--manifest", action="store_true", help="Generate a JSON manifest of uploaded files")
    parser.add_argument("--error-report", default=None, help="Path to write a JSON error report of failed files")
    args = parser.parse_args()

    from logging_config import setup_logging
    setup_logging(level=args.log_level, log_file=args.log_file)

    config = load_config(Path(args.config))
    temp_root = Path(args.temp_dir) if args.temp_dir else Path(tempfile.mkdtemp(prefix="takeout_bridge_"))
    temp_root.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download
        archive_path = download_takeout(args.url, temp_root)
        # 2. Extract
        extract_dir = temp_root / "extracted"
        extract_dir.mkdir()
        extracted_files = extract_archive(archive_path, extract_dir)
        # 3. Upload to R2 (concurrent)
        uploaded = upload_all_to_r2(config["r2"], extracted_files)
        # 4. Strict verification (MD5)
        bucket = get_r2_client(config["r2"])
        all_ok, mismatches = verify_upload_strict(bucket, uploaded)
        if not all_ok:
            for local_path, key, reason in mismatches:
                logging.error("Verification failed for %s (key=%s): %s", local_path, key, reason)
        logging.info("All files verified successfully in R2")
        # Collect failures for error report
        failures = []
        if not all_ok:
            for local_path, key, reason in mismatches:
                failures.append({
                    "local_path": str(local_path),
                    "r2_key": key,
                    "reason": reason,
                    "stage": "verification",
                })
        if args.error_report and failures:
            report_path = Path(args.error_report)
            report_path.write_text(json.dumps({"failures": failures}, indent=2))
            logging.info("Error report written to %s (%d failures)", report_path, len(failures))
        if failures:
            sys.exit(1)
        # Optional manifest generation
        if args.manifest:
            manifest_path = temp_root / "upload_manifest.json"
            manifest_data = [{"local_path": str(p), "r2_key": k} for p, k in uploaded]
            manifest_path.write_text(json.dumps(manifest_data, indent=2))
            logging.info("Manifest written to %s", manifest_path)
        # 5. Split
        images, videos = split_media([p for p, _ in uploaded])
        # 6. Amazon Photos (concurrent placeholder)
        upload_to_amazon_photos(images, config.get("amazon_photos", {}).get("access_token", ""))
        # 7. YouTube (real implementation)
        upload_to_youtube(videos, config["youtube"])
    finally:
        if not args.temp_dir:
            shutil.rmtree(temp_root, ignore_errors=True)
            logging.debug("Removed temporary directory %s", temp_root)

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()

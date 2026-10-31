#!/usr/bin/env python3
"""takeout-media-bridge

Automates migrating Google Photos (via Google Takeout export archive) to
Cloudflare R2, Amazon Photos, and YouTube:
1. Receive a Google Takeout export URL for Google Photos.
2. Download the archive and extract its contents.
3. Upload all files to a Cloudflare R2 bucket (S3-compatible).
4. Strictly verify that every file was uploaded with matching MD5 checksum.
5. Split the payload into images and videos.
6. Upload images to Amazon Photos.
7. Upload videos to YouTube via the Data API v3.
"""

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from amazon_photos import upload_to_amazon_photos
from config import load_config
from downloader import download_takeout
from extractor import extract_archive
from logging_config import setup_logging
from r2_client import get_r2_client, upload_all_to_r2
from splitter import split_media
from verifier import verify_upload_strict
from youtube_uploader import upload_to_youtube


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Takeout → R2 → Amazon Photos / YouTube bridge"
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Direct download URL for the Google Takeout archive (Google Photos export)",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to JSON configuration file (default: config.json)",
    )
    parser.add_argument(
        "--temp-dir",
        default=None,
        help="Optional directory for temporary files",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional path to a log file (with rotation)",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="Generate a JSON manifest of uploaded files",
    )
    parser.add_argument(
        "--error-report",
        default=None,
        help="Path to write a JSON error report of failed files",
    )
    args = parser.parse_args()

    setup_logging(level=args.log_level, log_file=args.log_file)

    config = load_config(Path(args.config))
    temp_root = (
        Path(args.temp_dir)
        if args.temp_dir
        else Path(tempfile.mkdtemp(prefix="takeout_bridge_"))
    )
    temp_root.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download
        archive_path = download_takeout(args.url, temp_root)

        # 2. Extract
        extract_dir = temp_root / "extracted"
        extract_dir.mkdir(exist_ok=True)
        extracted_files = extract_archive(archive_path, extract_dir)

        # 3. Upload to R2 (concurrent)
        uploaded = upload_all_to_r2(config["r2"], extracted_files)

        # 4. Strict verification (MD5)
        bucket = get_r2_client(config["r2"])
        all_ok, mismatches = verify_upload_strict(bucket, uploaded)
        failures = []
        if not all_ok:
            for local_path, key, reason in mismatches:
                logging.error(
                    "Verification failed for %s (key=%s): %s",
                    local_path,
                    key,
                    reason,
                )
                failures.append(
                    {
                        "local_path": str(local_path),
                        "r2_key": key,
                        "reason": reason,
                        "stage": "verification",
                    }
                )

        if args.error_report and failures:
            report_path = Path(args.error_report)
            report_path.write_text(json.dumps({"failures": failures}, indent=2))
            logging.info(
                "Error report written to %s (%d failures)",
                report_path,
                len(failures),
            )

        if failures:
            sys.exit(1)

        logging.info("All files verified successfully in R2")

        # Optional manifest generation
        if args.manifest:
            manifest_path = temp_root / "upload_manifest.json"
            manifest_data = [
                {"local_path": str(p), "r2_key": k} for p, k in uploaded
            ]
            manifest_path.write_text(json.dumps(manifest_data, indent=2))
            logging.info("Manifest written to %s", manifest_path)

        # 5. Split
        images, videos = split_media([p for p, _ in uploaded])

        # 6. Amazon Photos
        amazon_cfg = config.get("amazon_photos", {})
        upload_to_amazon_photos(
            images,
            amazon_cfg.get("access_token", ""),
            amazon_cfg.get("upload_endpoint", ""),
        )

        # 7. YouTube
        upload_to_youtube(videos, config["youtube"])
    finally:
        if not args.temp_dir:
            shutil.rmtree(temp_root, ignore_errors=True)
            logging.debug("Removed temporary directory %s", temp_root)


if __name__ == "__main__":
    main()

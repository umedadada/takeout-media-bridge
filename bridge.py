#!/usr/bin/env python3
"""takeout-media-bridge

Automates migrating Google Photos (via Google Takeout export archive) to
Cloudflare R2, Amazon Photos, and YouTube.

Supports two-phase execution:
- Phase 1 (Takeout -> R2 only):
    python bridge.py --url "<TAKEOUT_URL>" --r2-only
- Phase 2 (R2 -> Amazon Photos / YouTube):
    python bridge.py --from-r2
- Full pipeline in one run:
    python bridge.py --url "<TAKEOUT_URL>"
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
from r2_client import download_all_from_r2, get_r2_client, upload_all_to_r2
from splitter import split_media
from verifier import verify_upload_strict
from youtube_uploader import upload_to_youtube


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Takeout → R2 → Amazon Photos / YouTube bridge"
    )
    parser.add_argument(
        "--url",
        required=False,
        default=None,
        help="Direct download URL for the Google Takeout archive (required unless --from-r2 is set)",
    )
    parser.add_argument(
        "--r2-only",
        action="store_true",
        help="Phase 1 mode: Download Takeout, upload to R2, verify MD5, and exit (skip Amazon Photos & YouTube)",
    )
    parser.add_argument(
        "--from-r2",
        action="store_true",
        help="Phase 2 mode: Fetch already-uploaded media from R2 and process for Amazon Photos / YouTube",
    )
    parser.add_argument(
        "--r2-prefix",
        default="takeout/",
        help="R2 key prefix to use when uploading or fetching with --from-r2 (default: takeout/)",
    )
    parser.add_argument(
        "--amazon-sync-dir",
        default=None,
        help="Local folder path monitored by Amazon Photos desktop app (stages images here)",
    )
    parser.add_argument(
        "--skip-youtube",
        action="store_true",
        help="Skip YouTube video upload",
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

    if not args.from_r2 and not args.url:
        parser.error("--url is required unless --from-r2 is specified.")

    setup_logging(level=args.log_level, log_file=args.log_file)

    config = load_config(Path(args.config))
    temp_root = (
        Path(args.temp_dir)
        if args.temp_dir
        else Path(tempfile.mkdtemp(prefix="takeout_bridge_"))
    )
    temp_root.mkdir(parents=True, exist_ok=True)

    try:
        if args.from_r2:
            # Phase 2: Download files from R2 to local temp dir
            r2_download_dir = temp_root / "from_r2"
            r2_download_dir.mkdir(exist_ok=True)
            media_files = download_all_from_r2(
                config["r2"], r2_download_dir, prefix=args.r2_prefix
            )
        else:
            # Phase 1: Download Takeout -> Extract -> Upload to R2 -> Verify
            archive_path = download_takeout(args.url, temp_root)

            extract_dir = temp_root / "extracted"
            extract_dir.mkdir(exist_ok=True)
            extracted_files = extract_archive(archive_path, extract_dir)

            uploaded = upload_all_to_r2(config["r2"], extracted_files)

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

            if args.manifest:
                manifest_path = temp_root / "upload_manifest.json"
                manifest_data = [
                    {"local_path": str(p), "r2_key": k} for p, k in uploaded
                ]
                manifest_path.write_text(json.dumps(manifest_data, indent=2))
                logging.info("Manifest written to %s", manifest_path)

            if args.r2_only:
                logging.info(
                    "--r2-only specified: R2 upload and verification complete. Skipping Amazon Photos and YouTube."
                )
                return

            media_files = [p for p, _ in uploaded]

        # 5. Split into images and videos
        images, videos = split_media(media_files)

        # 6. Amazon Photos
        amazon_cfg = config.get("amazon_photos", {})
        sync_dir = args.amazon_sync_dir or amazon_cfg.get("sync_dir")
        upload_to_amazon_photos(
            images,
            token=amazon_cfg.get("access_token", ""),
            endpoint=amazon_cfg.get("upload_endpoint", ""),
            sync_dir=sync_dir,
        )

        # 7. YouTube
        if args.skip_youtube:
            logging.info("--skip-youtube specified: Skipping YouTube upload.")
        else:
            upload_to_youtube(videos, config["youtube"])
    finally:
        if not args.temp_dir:
            shutil.rmtree(temp_root, ignore_errors=True)
            logging.debug("Removed temporary directory %s", temp_root)


if __name__ == "__main__":
    main()

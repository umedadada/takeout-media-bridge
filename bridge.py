#!/usr/bin/env python3
"""takeout-media-bridge

Automates migrating Google Photos (via Google Takeout export archives) to
Cloudflare R2, Amazon Photos, and YouTube.

Supports multiple Takeout URLs or local archives, and two-phase execution:
- Phase 1 (Multiple Takeout URLs -> R2 only):
    python bridge.py --url "<URL1>" "<URL2>" --r2-only
    python bridge.py --urls-file urls.txt --r2-only
- Phase 2 (R2 -> Amazon Photos / YouTube):
    python bridge.py --from-r2
"""

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

from amazon_photos import upload_to_amazon_photos
from config import load_config
from downloader import download_takeout
from extractor import extract_archive
from logging_config import setup_logging
from r2_client import download_all_from_r2, get_r2_client, upload_all_to_r2
from splitter import split_media
from verifier import verify_upload_strict
from youtube_uploader import upload_to_youtube


def _collect_urls(args: argparse.Namespace) -> List[str]:
    """Gather all URLs from --url and --urls-file."""
    urls: List[str] = []
    if args.url:
        urls.extend(args.url)
    if args.urls_file:
        urls_path = Path(args.urls_file)
        if not urls_path.is_file():
            logging.error("URLs file not found: %s", urls_path)
            sys.exit(1)
        for line in urls_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                urls.append(stripped)
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Takeout → R2 → Amazon Photos / YouTube bridge"
    )
    parser.add_argument(
        "--url",
        nargs="+",
        required=False,
        default=None,
        help="One or more direct download URLs for Google Takeout archives",
    )
    parser.add_argument(
        "--urls-file",
        default=None,
        help="Path to a text file containing one Takeout download URL per line",
    )
    parser.add_argument(
        "--archive",
        nargs="+",
        default=None,
        help="One or more local Takeout archive files (.zip / .tgz) to process without downloading",
    )
    parser.add_argument(
        "--r2-only",
        action="store_true",
        help="Phase 1 mode: Download Takeout, upload to R2, verify MD5/ETag, and exit (skip Amazon Photos & YouTube)",
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

    urls = _collect_urls(args)
    local_archives = [Path(p).expanduser().resolve() for p in (args.archive or [])]

    if not args.from_r2 and not urls and not local_archives:
        parser.error(
            "Specify at least one --url, --urls-file, --archive, or use --from-r2."
        )

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
            # Phase 1: Process each URL and/or local archive one by one
            all_uploaded: List[Tuple[Path, str]] = []
            failures = []
            bucket = get_r2_client(config["r2"])

            total_sources = len(urls) + len(local_archives)
            source_items = [("url", u) for u in urls] + [
                ("archive", str(a)) for a in local_archives
            ]

            for idx, (src_type, src_val) in enumerate(source_items, start=1):
                logging.info(
                    "=== Processing archive %d/%d (%s) ===",
                    idx,
                    total_sources,
                    src_type,
                )
                batch_dir = temp_root / f"batch_{idx:03d}"
                batch_dir.mkdir(parents=True, exist_ok=True)

                try:
                    if src_type == "url":
                        archive_path = download_takeout(src_val, batch_dir)
                    else:
                        archive_path = Path(src_val)

                    extract_dir = batch_dir / "extracted"
                    extract_dir.mkdir(exist_ok=True)
                    extracted_files = extract_archive(archive_path, extract_dir)

                    uploaded = upload_all_to_r2(
                        config["r2"],
                        extracted_files,
                        base_dir=extract_dir,
                        key_prefix=args.r2_prefix,
                    )

                    all_ok, mismatches = verify_upload_strict(bucket, uploaded)
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
                                    "batch": idx,
                                }
                            )
                    all_uploaded.extend(uploaded)
                except Exception as exc:
                    logging.error(
                        "Failed processing archive %d/%d (%s): %s",
                        idx,
                        total_sources,
                        src_val,
                        exc,
                    )
                    failures.append(
                        {
                            "source": src_val,
                            "reason": str(exc),
                            "stage": "archive_processing",
                            "batch": idx,
                        }
                    )
                finally:
                    # If --r2-only and not keeping temp-dir, free disk space after each archive!
                    if args.r2_only and not args.temp_dir:
                        shutil.rmtree(batch_dir, ignore_errors=True)
                        logging.info(
                            "Cleaned up temporary files for archive %d/%d",
                            idx,
                            total_sources,
                        )

            if args.error_report and failures:
                report_path = Path(args.error_report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps({"failures": failures}, indent=2))
                logging.info(
                    "Error report written to %s (%d failures)",
                    report_path,
                    len(failures),
                )

            if args.manifest:
                manifest_path = (
                    Path("upload_manifest.json")
                    if args.r2_only
                    else temp_root / "upload_manifest.json"
                )
                manifest_data = [
                    {"local_path": str(p), "r2_key": k} for p, k in all_uploaded
                ]
                manifest_path.write_text(json.dumps(manifest_data, indent=2))
                logging.info("Manifest written to %s", manifest_path)

            if failures:
                logging.error(
                    "Completed with %d failure(s). Check logs or error report.",
                    len(failures),
                )
                sys.exit(1)

            logging.info(
                "All %d archive(s) (%d files total) verified successfully in R2!",
                total_sources,
                len(all_uploaded),
            )

            if args.r2_only:
                logging.info(
                    "--r2-only specified: R2 upload and verification complete. Skipping Amazon Photos and YouTube."
                )
                return

            media_files = [p for p, _ in all_uploaded]

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

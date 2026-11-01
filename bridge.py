#!/usr/bin/env python3
"""takeout-media-bridge

Automates migrating Google Photos (via Google Takeout export archives) to
Cloudflare R2, Amazon Photos, and YouTube.

Supports:
- Automated Chrome download directly from Takeout archive page:
    python bridge.py --takeout-page "https://takeout.google.com/manage/archive/..." --r2-only
- Multiple Takeout URLs or local archives:
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
from typing import Iterator, List, Tuple

from amazon_photos import upload_to_amazon_photos
from config import load_config
from downloader import download_takeout
from extractor import extract_archive
from logging_config import setup_logging
from r2_client import download_all_from_r2, get_r2_client, upload_all_to_r2
from splitter import split_media
from takeout_browser import iter_takeout_page_downloads
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


def _iter_archives(
    args: argparse.Namespace,
    urls: List[str],
    local_archives: List[Path],
    temp_root: Path,
    google_password: str = "",
) -> Iterator[Tuple[int, str, Path, Path]]:
    """Yield (batch_index, source_label, batch_dir, archive_path) for each archive."""
    idx = (args.start_from - 1) if args.takeout_page else 0
    if args.takeout_page:
        browser_dl_dir = temp_root / "browser_downloads"
        for dl_path in iter_takeout_page_downloads(
            page_url=args.takeout_page,
            download_dir=browser_dl_dir,
            cdp_url=args.chrome_cdp,
            start_from=args.start_from,
            google_password=google_password or None,
        ):
            idx += 1
            batch_dir = temp_root / f"batch_{idx:03d}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            # Move downloaded archive into batch_dir so batch cleanup removes it too
            target_archive = batch_dir / dl_path.name
            shutil.move(str(dl_path), str(target_archive))
            yield idx, f"takeout-page:{target_archive.name}", batch_dir, target_archive

    for u in urls:
        idx += 1
        batch_dir = temp_root / f"batch_{idx:03d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        archive_path = download_takeout(u, batch_dir)
        yield idx, u, batch_dir, archive_path

    for a in local_archives:
        idx += 1
        batch_dir = temp_root / f"batch_{idx:03d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        yield idx, str(a), batch_dir, a


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Takeout → R2 → Amazon Photos / YouTube bridge"
    )
    parser.add_argument(
        "--takeout-page",
        default=None,
        help="Google Takeout archive management URL (https://takeout.google.com/manage/archive/...) to auto-download via Chrome",
    )
    parser.add_argument(
        "--chrome-cdp",
        default=None,
        help="Optional Chrome DevTools Protocol URL (e.g. http://127.0.0.1:9222) to reuse an already-open Chrome",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=1,
        help="1-based archive index to resume from on the Takeout page (default: 1)",
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

    if not args.from_r2 and not args.takeout_page and not urls and not local_archives:
        parser.error(
            "Specify --takeout-page, --url, --urls-file, --archive, or use --from-r2."
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
            # Phase 1: Process each archive one by one
            all_uploaded: List[Tuple[Path, str]] = []
            failures = []
            bucket = get_r2_client(config["r2"])
            processed_batches = 0

            for idx, src_label, batch_dir, archive_path in _iter_archives(
                args,
                urls,
                local_archives,
                temp_root,
                google_password=config.get("google_password", ""),
            ):
                processed_batches += 1
                logging.info(
                    "=== Processing archive #%d (%s) ===",
                    idx,
                    src_label,
                )
                try:
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
                        "Failed processing archive #%d (%s): %s",
                        idx,
                        src_label,
                        exc,
                    )
                    failures.append(
                        {
                            "source": src_label,
                            "reason": str(exc),
                            "stage": "archive_processing",
                            "batch": idx,
                        }
                    )
                finally:
                    if args.r2_only and not args.temp_dir:
                        shutil.rmtree(batch_dir, ignore_errors=True)
                        logging.info(
                            "Cleaned up temporary files for archive #%d",
                            idx,
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
                processed_batches,
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

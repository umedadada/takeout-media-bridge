"""Extractor module for Takeout archive.

Provides `extract_archive` which extracts a zip or tar/tgz file and returns a
list of Path objects for each extracted file. If the downloaded file is a
standalone media file (e.g. a large .mp4 exported directly by Google Takeout),
it moves it into *extract_to* and returns it directly.
"""

import logging
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import List


def extract_archive(archive_path: Path, extract_to: Path) -> List[Path]:
    """Extract the Takeout archive (.zip, .tgz, .tar.gz) or handle raw media files."""
    logging.info("Extracting %s to %s", archive_path, extract_to)
    name_lower = archive_path.name.lower()
    if name_lower.endswith((".tgz", ".tar.gz", ".tar")):
        with tarfile.open(archive_path, "r:*") as t:
            t.extractall(extract_to)
            extracted = [
                extract_to / member.name
                for member in t.getmembers()
                if member.isfile()
            ]
    elif name_lower.endswith(".zip") and zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as z:
            z.extractall(extract_to)
            extracted = [
                extract_to / member
                for member in z.namelist()
                if not member.endswith("/")
            ]
        # Delete the .zip immediately after extraction to free disk space!
        try:
            archive_path.unlink()
        except OSError:
            pass
    else:
        # Standalone file (e.g., large .mp4 / .mov exported outside zip by Takeout)
        # Strip any leading "025_" batch prefix if added by browser downloader
        clean_name = archive_path.name
        if len(clean_name) > 4 and clean_name[:3].isdigit() and clean_name[3] == "_":
            clean_name = clean_name[4:]
        dest_file = extract_to / clean_name
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archive_path), str(dest_file))
        extracted = [dest_file]
        logging.info("Treated standalone file %s as direct media item", dest_file.name)

    logging.info("Prepared %d file(s)", len(extracted))
    return extracted

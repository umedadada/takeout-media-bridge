"""Extractor module for Takeout archive.

Provides `extract_archive` which extracts a zip or tar/tgz file and returns a
list of Path objects for each extracted file.
"""

import logging
import tarfile
import zipfile
from pathlib import Path
from typing import List


def extract_archive(archive_path: Path, extract_to: Path) -> List[Path]:
    """Extract the Takeout archive (.zip, .tgz, .tar.gz) and return extracted files."""
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
    else:
        with zipfile.ZipFile(archive_path, "r") as z:
            z.extractall(extract_to)
            extracted = [
                extract_to / member
                for member in z.namelist()
                if not member.endswith("/")
            ]
    logging.info("Extracted %d files", len(extracted))
    return extracted

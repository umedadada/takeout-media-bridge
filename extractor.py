"""Extractor module for Takeout archive.

Provides `extract_archive` which extracts a zip file and returns a list of
Path objects for each extracted file.
"""

import logging
import zipfile
from pathlib import Path
from typing import List


def extract_archive(archive_path: Path, extract_to: Path) -> List[Path]:
    """Extract the Takeout zip file and return a list of all extracted files."""
    logging.info("Extracting %s to %s", archive_path, extract_to)
    with zipfile.ZipFile(archive_path, "r") as z:
        z.extractall(extract_to)
        extracted = [extract_to / member for member in z.namelist() if not member.endswith('/')]
    logging.info("Extracted %d files", len(extracted))
    return extracted

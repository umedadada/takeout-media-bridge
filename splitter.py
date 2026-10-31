"""Splitter utilities.

Provides `split_media` to separate image and video files based on their extensions.
"""

import logging
from pathlib import Path
from typing import List, Tuple


def split_media(files: List[Path]) -> Tuple[List[Path], List[Path]]:
    """Split a list of file paths into images and videos.

    Returns a tuple `(images, videos)`.
    """
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

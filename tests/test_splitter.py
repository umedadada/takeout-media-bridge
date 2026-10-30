"""Unit tests for splitter module."""
import pytest
from pathlib import Path
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from splitter import split_media


class TestSplitMedia:
    def test_splits_images_and_videos(self, tmp_path):
        files = [
            tmp_path / "photo.jpg",
            tmp_path / "clip.mp4",
            tmp_path / "pic.png",
            tmp_path / "movie.mov",
            tmp_path / "unknown.txt",
        ]
        for f in files:
            f.touch()

        images, videos = split_media(files)

        assert len(images) == 2
        assert len(videos) == 2
        assert tmp_path / "photo.jpg" in images
        assert tmp_path / "clip.mp4" in videos

    def test_empty_input(self):
        images, videos = split_media([])
        assert images == []
        assert videos == []

    def test_heic_is_image(self, tmp_path):
        f = tmp_path / "shot.HEIC"
        f.touch()
        images, videos = split_media([f])
        assert f in images

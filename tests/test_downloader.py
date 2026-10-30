"""Unit tests for downloader module."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from downloader import download_takeout


class TestDownloadTakeout:
    def test_downloads_file(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-disposition": 'attachment; filename="takeout.zip"'}
        mock_resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_resp.raise_for_status.return_value = None

        with patch("downloader.requests.get", return_value=mock_resp):
            result = download_takeout("http://example.com/takeout.zip", tmp_path)

        assert result == tmp_path / "takeout.zip"
        assert result.exists()

    def test_fallback_filename(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.iter_content.return_value = [b"data"]
        mock_resp.raise_for_status.return_value = None

        with patch("downloader.requests.get", return_value=mock_resp):
            result = download_takeout("http://example.com/file", tmp_path)

        assert result.name == "takeout_archive.zip"

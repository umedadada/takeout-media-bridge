"""Unit tests for amazon_photos module."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from amazon_photos import upload_to_amazon_photos, _upload_one


class TestUploadToAmazonPhotos:
    def test_skips_when_no_token(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            upload_to_amazon_photos([], token="", endpoint="http://example.com")
        assert "token not provided" in caplog.text

    def test_skips_when_no_endpoint(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR):
            upload_to_amazon_photos([], token="tok", endpoint="")
        assert "endpoint not configured" in caplog.text

    def test_uploads_successfully(self, tmp_path):
        img = tmp_path / "img.jpg"
        img.write_bytes(b"fake image")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("amazon_photos.requests.post", return_value=mock_response):
            upload_to_amazon_photos([img], token="tok", endpoint="http://example.com/upload")

    def test_stages_to_sync_dir(self, tmp_path):
        img = tmp_path / "img.jpg"
        img.write_bytes(b"fake image")
        sync_dir = tmp_path / "sync_folder"

        upload_to_amazon_photos([img], sync_dir=str(sync_dir))

        assert (sync_dir / "img.jpg").read_bytes() == b"fake image"


class TestUploadOne:
    def test_success(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("amazon_photos.requests.post", return_value=mock_resp):
            result = _upload_one(img, "token", "http://endpoint")

        assert result is True

    def test_failure_returns_false(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"data")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("amazon_photos.requests.post", return_value=mock_resp):
            result = _upload_one(img, "token", "http://endpoint")

        assert result is False

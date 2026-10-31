"""Unit tests for r2_client module."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from r2_client import get_r2_client, upload_to_r2, upload_all_to_r2, object_exists, _TRANSFER_CONFIG


class TestGetR2Client:
    def test_returns_bucket(self):
        cfg = {
            "endpoint_url": "https://test.r2.cloudflarestorage.com",
            "access_key_id": "key",
            "secret_access_key": "secret",
            "bucket": "test-bucket",
        }
        with patch("r2_client.boto3") as mock_boto:
            mock_session = MagicMock()
            mock_boto.session.Session.return_value = mock_session
            mock_s3 = MagicMock()
            mock_session.resource.return_value = mock_s3
            mock_bucket = MagicMock()
            mock_s3.Bucket.return_value = mock_bucket

            result = get_r2_client(cfg)

            mock_session.resource.assert_called_once_with(
                service_name="s3",
                endpoint_url=cfg["endpoint_url"],
                aws_access_key_id=cfg["access_key_id"],
                aws_secret_access_key=cfg["secret_access_key"],
            )
            mock_s3.Bucket.assert_called_once_with("test-bucket")
            assert result == mock_bucket


class TestUploadToR2:
    def test_uploads_file_and_returns_key(self, tmp_path):
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"fake image data")
        mock_bucket = MagicMock()

        key = upload_to_r2(mock_bucket, test_file, key_prefix="takeout/")

        assert key == "takeout/photo.jpg"
        mock_bucket.upload_file.assert_called_once_with(
            str(test_file), "takeout/photo.jpg", Config=_TRANSFER_CONFIG
        )

    def test_retries_on_exception(self, tmp_path):
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"data")
        mock_bucket = MagicMock()
        mock_bucket.upload_file.side_effect = [Exception("transient"), None]

        with patch("utils.time.sleep"):
            key = upload_to_r2(mock_bucket, test_file, key_prefix="")

        assert mock_bucket.upload_file.call_count == 2


class TestUploadAllToR2:
    def test_uploads_all_files(self, tmp_path):
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.jpg"
            f.write_bytes(b"data")
            files.append(f)

        cfg = {
            "endpoint_url": "https://test.r2.cloudflarestorage.com",
            "access_key_id": "key",
            "secret_access_key": "secret",
            "bucket": "test-bucket",
        }
        with patch("r2_client.get_r2_client") as mock_client, \
             patch("r2_client.upload_to_r2") as mock_upload:
            mock_bucket = MagicMock()
            mock_client.return_value = mock_bucket
            mock_upload.side_effect = lambda bucket, f, key_prefix="": f"takeout/{f.name}"

            result = upload_all_to_r2(cfg, files)

        assert len(result) == 3


class TestObjectExists:
    def test_returns_true_when_object_exists(self):
        mock_bucket = MagicMock()
        mock_bucket.Object.return_value.load.return_value = None

        assert object_exists(mock_bucket, "some/key") is True

    def test_returns_false_when_object_missing(self):
        mock_bucket = MagicMock()
        mock_bucket.Object.return_value.load.side_effect = Exception("Not Found")

        assert object_exists(mock_bucket, "missing/key") is False

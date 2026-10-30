"""Unit tests for config module."""
import pytest
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path):
        cfg_data = {
            "r2": {
                "endpoint_url": "https://test.r2.cf.com",
                "access_key_id": "key",
                "secret_access_key": "secret",
                "bucket": "bucket",
            },
            "youtube": {
                "client_secrets_file": "client.json",
                "credentials_file": "creds.json",
            },
            "amazon_photos": {
                "access_token": "tok",
                "upload_endpoint": "http://endpoint",
            },
        }
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        from config import load_config
        result = load_config(cfg_file)
        assert result["r2"]["bucket"] == "bucket"

    def test_env_vars_override_config(self, tmp_path, monkeypatch):
        cfg_data = {
            "r2": {
                "endpoint_url": "https://test.r2.cf.com",
                "access_key_id": "key",
                "secret_access_key": "secret",
                "bucket": "bucket",
            },
            "youtube": {
                "client_secrets_file": "client.json",
                "credentials_file": "creds.json",
            },
            "amazon_photos": {
                "access_token": "tok",
                "upload_endpoint": "http://endpoint",
            },
        }
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        monkeypatch.setenv("R2_BUCKET", "env-bucket")

        import importlib
        import config as cfg_module
        importlib.reload(cfg_module)
        from config import load_config
        result = load_config(cfg_file)
        assert result["r2"]["bucket"] == "env-bucket"

    def test_missing_file_exits(self, tmp_path):
        from config import load_config
        with pytest.raises(SystemExit):
            load_config(tmp_path / "nonexistent.json")

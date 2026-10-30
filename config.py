"""Configuration loader and validator for takeout-media-bridge.

Provides `load_config` which reads a JSON file and validates required keys using
`jsonschema`.  Validation errors raise a clear `ValueError` so the CLI can abort
early.
"""

import json
import logging
import os
import sys
from pathlib import Path

# Optional: add jsonschema for strict validation. If the package is not
# installed we fall back to a minimal key‑presence check.
try:
    from jsonschema import Draft7Validator
except ImportError:  # pragma: no cover
    Draft7Validator = None

CONFIG_SCHEMA = {
    "type": "object",
    "required": ["r2", "youtube", "amazon_photos"],
    "properties": {
        "r2": {
            "type": "object",
            "required": ["endpoint_url", "access_key_id", "secret_access_key", "bucket"],
        },
        "youtube": {
            "type": "object",
            "required": ["client_secrets_file", "credentials_file"],
        },
        "amazon_photos": {
            "type": "object",
            "required": ["access_token", "upload_endpoint"],
        },
    },
}


def _validate(config: dict) -> None:
    """Validate the loaded configuration.

    If ``jsonschema`` is unavailable, perform a lightweight existence check.
    """
    if Draft7Validator is None:
        # Simple presence check – raise if any required top‑level key is missing.
        missing = [k for k in CONFIG_SCHEMA["required"] if k not in config]
        if missing:
            raise ValueError(f"Missing required config sections: {missing}")
        return
    validator = Draft7Validator(CONFIG_SCHEMA)
    errors = sorted(validator.iter_errors(config), key=lambda e: e.path)
    if errors:
        msgs = "; ".join([f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors])
        raise ValueError(f"Configuration validation errors: {msgs}")


def load_config(config_path: Path) -> dict:
    """Load JSON configuration from *config_path* and validate it.

    The function exits the process with an error message if the file cannot be
    read or validation fails.
    """
    if not config_path.is_file():
        logging.error("Config file %s not found", config_path)
        sys.exit(1)
    with config_path.open() as f:
        cfg = json.load(f)
    # Merge environment variables (override config file)
    env_map = {
        "r2": {
            "endpoint_url": os.getenv("R2_ENDPOINT"),
            "access_key_id": os.getenv("R2_KEY_ID"),
            "secret_access_key": os.getenv("R2_SECRET"),
            "bucket": os.getenv("R2_BUCKET"),
        },
        "youtube": {
            "client_secrets_file": os.getenv("YOUTUBE_CLIENT_SECRETS"),
            "credentials_file": os.getenv("YOUTUBE_CREDENTIALS"),
        },
        "amazon_photos": {
            "access_token": os.getenv("AMAZON_PHOTOS_TOKEN"),
            "upload_endpoint": os.getenv("AMAZON_PHOTOS_ENDPOINT"),
        },
    }
    for section, values in env_map.items():
        if section not in cfg:
            cfg[section] = {}
        for key, val in values.items():
            if val is not None:
                cfg[section][key] = val
    try:
        _validate(cfg)
    except ValueError as e:
        logging.error("Invalid configuration: %s", e)
        sys.exit(1)
    return cfg

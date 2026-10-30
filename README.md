# takeout-media-bridge

Automate the migration of Google Photos (via Google Takeout export archive) to Cloudflare R2, Amazon Photos, and YouTube.

---

## 🌟 Features

- 📥 **Takeout Download**: Download large Google Takeout archives directly via URL with automatic retry on transient errors (e.g. 503).
- 📦 **Archive Extraction**: Extract ZIP archives and enumerate media contents.
- ☁️ **Cloudflare R2 Upload**: Upload all files to Cloudflare R2 (S3-compatible) concurrently with multipart threshold for large files.
- 🔍 **Strict Verification**: Validate uploaded files against original local files using MD5 checksums / ETags.
- 🖼️ **Amazon Photos Integration**: Filter image files (`.jpg`, `.png`, `.heic`, `.gif`, etc.) and upload concurrently with retry.
- 🎬 **YouTube Upload**: Filter video files (`.mp4`, `.mov`, `.avi`, `.mkv`, etc.) and upload using YouTube Data API v3 with resumable upload chunks.
- 📊 **Progress Bars**: Visual progress monitoring via `tqdm`.
- 🔁 **Exponential Backoff**: Resilient upload and download handling with `@retry` decorator.
- 🐳 **Docker Support**: Containerized execution with a lightweight Python image.
- 📝 **Rotating File Logs**: Optional `--log-file` support for long-running batch migrations.
- 🚨 **Error Reporting**: Optional `--error-report` JSON output for failed files to facilitate manual retries.

---

## 📋 Prerequisites

- **Python**: 3.10 or 3.11+
- **Cloudflare R2**: Account ID, Access Key ID, Secret Access Key, and bucket name.
- **Google Cloud / YouTube API**: A Google Cloud project with the **YouTube Data API v3** enabled and an OAuth 2.0 Client ID credentials file (`client_secret.json`).
- **Amazon Photos**: An OAuth2 access token and upload endpoint.

---

## 🚀 Installation

### Local Environment

```bash
git clone https://github.com/umedadada/takeout-media-bridge.git
cd takeout-media-bridge

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Docker

```bash
docker build -t takeout-media-bridge .
```

---

## ⚙️ Configuration

Create a `config.json` file in the project directory (or specify another path with `--config`):

```json
{
  "r2": {
    "endpoint_url": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com",
    "access_key_id": "<R2_ACCESS_KEY_ID>",
    "secret_access_key": "<R2_SECRET_ACCESS_KEY>",
    "bucket": "takeout-media-archive"
  },
  "youtube": {
    "client_secrets_file": "client_secret.json",
    "credentials_file": "youtube_credentials.json"
  },
  "amazon_photos": {
    "access_token": "<AMAZON_PHOTOS_OAUTH_TOKEN>",
    "upload_endpoint": "https://photos.amazon.com/api/v1/uploads"
  }
}
```

### Environment Variable Overrides

All settings can be provided or overridden using environment variables:

| Environment Variable | Config Path | Description |
|----------------------|-------------|-------------|
| `R2_ENDPOINT` | `r2.endpoint_url` | Cloudflare R2 S3 endpoint URL |
| `R2_KEY_ID` | `r2.access_key_id` | Cloudflare R2 Access Key ID |
| `R2_SECRET` | `r2.secret_access_key` | Cloudflare R2 Secret Access Key |
| `R2_BUCKET` | `r2.bucket` | Cloudflare R2 Bucket name |
| `YOUTUBE_CLIENT_SECRETS` | `youtube.client_secrets_file` | Path to `client_secret.json` |
| `YOUTUBE_CREDENTIALS` | `youtube.credentials_file` | Path to stored OAuth credentials |
| `AMAZON_PHOTOS_TOKEN` | `amazon_photos.access_token` | Amazon Photos OAuth token |
| `AMAZON_PHOTOS_ENDPOINT` | `amazon_photos.upload_endpoint` | Amazon Photos upload API endpoint |

---

## 💻 Usage

### Basic Execution

```bash
python bridge.py \
  --url "https://storage.googleapis.com/takeout-download-link..." \
  --config config.json
```

### With File Logging and Manifest

```bash
python bridge.py \
  --url "https://storage.googleapis.com/takeout-download-link..." \
  --config config.json \
  --log-level DEBUG \
  --log-file logs/bridge.log \
  --manifest
```

### With Error Reporting

```bash
python bridge.py \
  --url "https://storage.googleapis.com/takeout-download-link..." \
  --config config.json \
  --error-report failed_uploads.json
```

### Docker Execution

```bash
docker run --rm \
  -v $(pwd)/config.json:/app/config.json \
  -v $(pwd)/client_secret.json:/app/client_secret.json \
  -v $(pwd)/logs:/app/logs \
  -e R2_ENDPOINT="${R2_ENDPOINT}" \
  -e R2_KEY_ID="${R2_KEY_ID}" \
  -e R2_SECRET="${R2_SECRET}" \
  takeout-media-bridge \
  --url "https://storage.googleapis.com/takeout-download-link..." \
  --config config.json \
  --log-file /app/logs/bridge.log
```

---

## 🛠️ CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--url` | *(required)* | Direct download URL for the Google Takeout archive |
| `--config` | `config.json` | Path to JSON configuration file |
| `--temp-dir` | *(auto)* | Directory for temporary files (cleaned up on exit) |
| `--log-level` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `--log-file` | *(none)* | Optional path to write rotating log files |
| `--manifest` | `False` | Generate a JSON manifest of uploaded files |
| `--error-report` | *(none)* | Path to write a JSON error report of failed files |

---

## 🧪 Testing

Run unit tests with pytest:

```bash
pip install -r requirements.txt
pytest -v
```

---

## ❓ Troubleshooting

### 1. `Configuration validation errors`
Verify that `config.json` has all required fields (`r2`, `youtube`, `amazon_photos`), or that corresponding environment variables are set.

### 2. YouTube OAuth Authorization
On the first run, the script opens a browser window or provides a link to authenticate with Google. Authorized tokens are stored in `youtube_credentials.json` for subsequent runs. In headless/Docker environments, authorize locally first and mount `youtube_credentials.json`.

### 3. Verification Mismatch on Large Files
Files larger than 8MB use S3 multipart upload. The ETag for multipart objects in S3/R2 reflects part checksums rather than a single MD5 hash. Ensure proper multipart verification or inspect `--error-report` output.

---

## 📄 License

MIT License

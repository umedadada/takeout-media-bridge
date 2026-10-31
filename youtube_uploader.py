"""YouTube uploader module for Takeout media bridge.

Provides `upload_to_youtube` which uploads video files to YouTube using the
Data API v3, with OAuth2 token handling and retry on transient errors.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import List, Union

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

from utils import retry

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CHUNK_SIZE = 256 * 1024  # 256 KB resumable-upload chunks


def _build_youtube_client(credentials: Credentials):
    """Build and return an authenticated YouTube API client."""
    return build("youtube", "v3", credentials=credentials)


def _get_youtube_credentials(youtube_cfg: dict) -> Credentials:
    """Load or obtain OAuth2 credentials from *youtube_cfg*."""
    scopes = [YOUTUBE_UPLOAD_SCOPE]
    creds = None
    cred_path = Path(youtube_cfg.get("credentials_file", "youtube_credentials.json"))
    if cred_path.is_file():
        creds = Credentials.from_authorized_user_file(str(cred_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logging.warning("Failed to refresh YouTube token: %s", e)
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                youtube_cfg["client_secrets_file"], scopes
            )
            creds = flow.run_local_server(port=0)
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            cred_path.write_text(creds.to_json())
            logging.info("Saved new YouTube credentials to %s", cred_path)
    return creds


@retry(attempts=3, backoff=2)
def upload_video_to_youtube(
    credentials: Credentials,
    file_path: Path,
    title: str = "",
    description: str = "",
    privacy_status: str = "private",
) -> str:
    """Upload a single video file to YouTube and return the video ID."""
    if not title:
        title = file_path.stem
    if not description:
        description = f"Uploaded by takeout-media-bridge on {datetime.now(timezone.utc).isoformat()}"

    logging.info("Uploading %s to YouTube as '%s' (%s)", file_path, title, privacy_status)

    youtube = _build_youtube_client(credentials)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["takeout", "google-photos"],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    media = MediaFileUpload(
        str(file_path),
        chunksize=CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logging.debug("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    logging.info("Upload complete. YouTube video ID: %s", video_id)
    return video_id


def upload_to_youtube(
    videos_or_creds: Union[List[Path], Credentials],
    cfg_or_file: Union[dict, Path],
    title: str = "",
    description: str = "",
    privacy_status: str = "private",
):
    """Upload videos to YouTube.

    Supports both:
    - ``upload_to_youtube(video_files: List[Path], youtube_cfg: dict)`` (batch pipeline)
    - ``upload_to_youtube(credentials: Credentials, file_path: Path, ...)`` (single file)
    """
    if isinstance(videos_or_creds, Credentials):
        return upload_video_to_youtube(
            videos_or_creds,
            Path(cfg_or_file),
            title=title,
            description=description,
            privacy_status=privacy_status,
        )

    video_files: List[Path] = videos_or_creds
    youtube_cfg: dict = cfg_or_file
    if not video_files:
        logging.info("No video files to upload to YouTube.")
        return []

    creds = _get_youtube_credentials(youtube_cfg)
    uploaded_ids = []
    for video_path in video_files:
        try:
            vid = upload_video_to_youtube(
                creds,
                video_path,
                title=title,
                description=description,
                privacy_status=privacy_status,
            )
            uploaded_ids.append(vid)
        except Exception as e:
            logging.error("Failed to upload %s to YouTube: %s", video_path, e)
    return uploaded_ids

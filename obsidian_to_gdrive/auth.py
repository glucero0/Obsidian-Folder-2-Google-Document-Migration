import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .constants import APPLICATION_NAME, SCOPES


def get_google_services(credentials_path: str, token_path: str):
    """Authenticate via OAuth and return (docs_service, drive_service)."""
    creds = None
    token_file = Path(token_path)

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return docs_service, drive_service


def validate_configuration(note_folder_path: str, credentials_path: str, token_folder: str) -> None:
    if not os.path.isdir(note_folder_path):
        raise FileNotFoundError(f"Note folder path not found: {note_folder_path}")
    if not os.path.isfile(credentials_path):
        raise FileNotFoundError(f"Credentials file not found: {credentials_path}")
    os.makedirs(token_folder, exist_ok=True)

"""
Google Drive storage for claim files.

One Drive folder per claim; the database stores only the Drive file ID and
metadata. When GOOGLE_DRIVE_CREDENTIALS_FILE is not configured (local
development), files fall back to MEDIA_ROOT and drive_file_id stays empty —
the rest of the app doesn't care which backend stored the bytes.

To enable Drive, install the client libraries:
    pip install google-api-python-client google-auth
and set GOOGLE_DRIVE_CREDENTIALS_FILE + GOOGLE_DRIVE_ROOT_FOLDER_ID.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass
class StoredFile:
    drive_file_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    web_view_link: str


def drive_enabled() -> bool:
    return bool(settings.GOOGLE_DRIVE_CREDENTIALS_FILE)


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_DRIVE_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def ensure_claim_folder(claim) -> str:
    """Return the claim's Drive folder ID, creating the folder if needed."""
    if claim.drive_folder_id:
        return claim.drive_folder_id
    service = _service()
    folder = (
        service.files()
        .create(
            body={
                "name": claim.reference,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [settings.GOOGLE_DRIVE_ROOT_FOLDER_ID],
            },
            fields="id",
        )
        .execute()
    )
    claim.drive_folder_id = folder["id"]
    claim.save(update_fields=["drive_folder_id"])
    return claim.drive_folder_id


def upload_file(claim, uploaded_file) -> StoredFile:
    """Upload a Django UploadedFile into the claim's Drive folder."""
    from googleapiclient.http import MediaIoBaseUpload

    service = _service()
    folder_id = ensure_claim_folder(claim)
    media = MediaIoBaseUpload(
        uploaded_file.file,
        mimetype=uploaded_file.content_type or "application/octet-stream",
        resumable=True,
    )
    created = (
        service.files()
        .create(
            body={"name": uploaded_file.name, "parents": [folder_id]},
            media_body=media,
            fields="id, name, mimeType, size, webViewLink",
        )
        .execute()
    )
    return StoredFile(
        drive_file_id=created["id"],
        file_name=created["name"],
        mime_type=created.get("mimeType", ""),
        size_bytes=int(created.get("size", 0)),
        web_view_link=created.get("webViewLink", ""),
    )

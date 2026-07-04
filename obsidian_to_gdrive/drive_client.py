import base64
import binascii
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from googleapiclient.http import MediaFileUpload

from .constants import MIGRATION_COMPLETE_MARKER, SUPPORTED_IMAGE_EXTENSIONS
from .throttle import throttle_and_execute


@dataclass
class DriveDocInfo:
    id: str
    description: str | None = None


@dataclass
class UploadedDriveImage:
    file_id: str
    public_permission_id: str = ""


def escape_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def is_migration_complete(description: str | None) -> bool:
    return description is not None and MIGRATION_COMPLETE_MARKER in description


def find_drive_doc(drive_service, name: str, parent_id: str) -> DriveDocInfo | None:
    query = (
        f"name = '{escape_drive_query(name)}' "
        f"and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.document' "
        f"and trashed = false"
    )

    def _call():
        return (
            drive_service.files()
            .list(q=query, fields="files(id,description)", pageSize=1)
            .execute()
        )

    result = throttle_and_execute(_call)
    files = result.get("files", [])
    if not files:
        return None
    return DriveDocInfo(id=files[0]["id"], description=files[0].get("description"))


def stamp_migration_complete(drive_service, doc_id: str) -> None:
    throttle_and_execute(
        lambda: drive_service.files()
        .update(fileId=doc_id, body={"description": MIGRATION_COMPLETE_MARKER})
        .execute()
    )


def delete_drive_file(drive_service, file_id: str) -> None:
    throttle_and_execute(lambda: drive_service.files().delete(fileId=file_id).execute())


def ensure_drive_folder(drive_service, name: str, parent_id: str | None) -> str:
    if parent_id is None:
        query = (
            f"name = '{escape_drive_query(name)}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and 'root' in parents and trashed = false"
        )
    else:
        query = (
            f"name = '{escape_drive_query(name)}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and '{parent_id}' in parents and trashed = false"
        )

    def _list():
        return (
            drive_service.files()
            .list(q=query, fields="files(id)", pageSize=1)
            .execute()
        )

    existing = throttle_and_execute(_list)
    files = existing.get("files", [])
    if files:
        return files[0]["id"]

    metadata: dict = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id is not None:
        metadata["parents"] = [parent_id]

    def _create():
        return (
            drive_service.files()
            .create(body=metadata, fields="id")
            .execute()
        )

    created = throttle_and_execute(_create)
    return created["id"]


def ensure_doc_images_folder(drive_service, parent_folder_id: str, doc_title: str) -> str:
    """Per-document image folder: <parent>/_images/<doc_title>/."""
    images_root = ensure_drive_folder(drive_service, "_images", parent_folder_id)
    return ensure_drive_folder(drive_service, doc_title, images_root)


def ensure_drive_folder_path(drive_service, root_parent_id: str, relative_path: str) -> str:
    current_parent_id = root_parent_id
    for segment in relative_path.replace("\\", "/").split("/"):
        if segment:
            current_parent_id = ensure_drive_folder(drive_service, segment, current_parent_id)
    return current_parent_id


def ensure_my_drive_folder_path(drive_service, relative_path: str) -> str:
    """Ensure a folder path exists under My Drive and return its folder id."""
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return "root"
    return ensure_drive_folder_path(drive_service, "root", normalized)


def get_image_mime_type(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    return SUPPORTED_IMAGE_EXTENSIONS.get(ext)


_DATA_URI_PATTERN = re.compile(
    r"data:(image/(?:png|jpeg|jpg|gif));base64,([A-Za-z0-9+/=\s]+)",
    re.IGNORECASE | re.DOTALL,
)


def materialize_data_uri_image(data_uri: str) -> str | None:
    """Write a supported data: image URI to a temporary file for Drive upload."""
    match = _DATA_URI_PATTERN.match(data_uri.strip())
    if not match:
        return None

    mime = match.group(1).lower().replace("jpg", "jpeg")
    extension = next(
        (ext for ext, supported in SUPPORTED_IMAGE_EXTENSIONS.items() if supported == mime),
        None,
    )
    if extension is None:
        return None

    try:
        image_bytes = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error):
        return None

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        prefix="obsidian_embed_",
        suffix=extension,
    )
    try:
        temp_file.write(image_bytes)
        temp_file.close()
        return temp_file.name
    except OSError:
        temp_file.close()
        os.unlink(temp_file.name)
        return None


def format_missing_image_text(alt_text: str, img_ref: str) -> str:
    if img_ref.lower().startswith("data:") or len(img_ref) > 200:
        return f"[Image not found: {alt_text}]\n"
    return f"[Image not found: {alt_text} ({img_ref})]\n"


def upload_image(drive_service, image_path: str, parent_folder_id: str) -> UploadedDriveImage | None:
    mime = get_image_mime_type(image_path)
    if mime is None:
        print(f"  Skipping unsupported image type: {image_path}")
        return None

    metadata = {
        "name": os.path.basename(image_path),
        "parents": [parent_folder_id],
    }
    media = MediaFileUpload(image_path, mimetype=mime, resumable=True)

    def _upload():
        return (
            drive_service.files()
            .create(body=metadata, media_body=media, fields="id")
            .execute()
        )

    try:
        uploaded = throttle_and_execute(_upload)
    except Exception as exc:
        print(f"  Upload failed for '{image_path}': {exc}")
        return None

    file_id = uploaded.get("id")
    if not file_id:
        return None

    def _permission():
        return (
            drive_service.permissions()
            .create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            )
            .execute()
        )

    permission = throttle_and_execute(_permission)
    return UploadedDriveImage(file_id=file_id, public_permission_id=permission.get("id", ""))


def try_revoke_public_image_access(drive_service, uploaded: UploadedDriveImage) -> bool:
    if not uploaded.public_permission_id:
        return True
    try:
        throttle_and_execute(
            lambda: drive_service.permissions()
            .delete(fileId=uploaded.file_id, permissionId=uploaded.public_permission_id)
            .execute()
        )
        uploaded.public_permission_id = ""
        return True
    except Exception as exc:
        print(f"  Warning: could not revoke public access for image '{uploaded.file_id}': {exc}")
        return False


def retry_pending_image_revocations(drive_service, pending: list[UploadedDriveImage]) -> None:
    if not pending:
        return

    still_pending: list[UploadedDriveImage] = []
    for uploaded in pending:
        if not try_revoke_public_image_access(drive_service, uploaded):
            still_pending.append(uploaded)

    pending.clear()
    pending.extend(still_pending)

    if still_pending:
        ids = ", ".join(img.file_id for img in still_pending)
        print(f"  Warning: {len(still_pending)} uploaded image(s) remain publicly accessible: {ids}")


def resolve_image_path(
    img_ref: str,
    note_directory: str,
    vault_path: str,
    attachment_folder_setting: str | None = None,
) -> str | None:
    img_ref = unquote(img_ref.strip())
    if img_ref.lower().startswith(("http://", "https://")):
        return None

    img_ref = img_ref.split("|")[0].strip().replace("/", os.sep)

    candidates: list[str] = []
    if os.path.isabs(img_ref):
        candidates.append(img_ref)
    else:
        candidates.append(os.path.join(note_directory, img_ref))
        candidates.append(os.path.join(vault_path, img_ref))

        from .vault import resolve_attachment_directory

        attachment_dir = resolve_attachment_directory(
            vault_path, note_directory, attachment_folder_setting
        )
        if attachment_dir:
            candidates.append(os.path.join(attachment_dir, img_ref))
            if os.sep not in img_ref:
                candidates.append(os.path.join(attachment_dir, os.path.basename(img_ref)))

    seen: set[str] = set()
    for candidate in candidates:
        path = os.path.normpath(candidate)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            return path

    return None

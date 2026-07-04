import json
import os
from pathlib import Path


def collect_root_markdown_files(note_folder_path: str) -> list[str]:
    folder = os.path.normpath(note_folder_path)
    return sorted(
        [
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.lower().endswith(".md") and os.path.isfile(os.path.join(folder, name))
        ],
        key=str.lower,
    )


def find_obsidian_vault_root(note_folder_path: str) -> str:
    """Walk up from the note folder to find an Obsidian vault root (.obsidian/app.json)."""
    current = Path(note_folder_path).resolve()
    while True:
        if (current / ".obsidian" / "app.json").is_file():
            return str(current)
        parent = current.parent
        if parent == current:
            return os.path.normpath(note_folder_path)
        current = parent


def split_drive_parent_path(drive_relative_path: str) -> tuple[str, str]:
    """Split a Drive path into (parent_path, leaf_name).

    The leaf name becomes the Google Doc title. Parent folders are created under
    My Drive; the doc itself is not placed inside a same-named leaf folder.
    """
    normalized = drive_relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return "", ""
    parts = normalized.split("/")
    if len(parts) == 1:
        return "", parts[0]
    return "/".join(parts[:-1]), parts[-1]


def make_tab_title(file_path: str) -> str:
    return Path(file_path).stem


def load_obsidian_attachment_folder(vault_path: str) -> str | None:
    """Read Obsidian's attachmentFolderPath from .obsidian/app.json."""
    app_json = Path(vault_path) / ".obsidian" / "app.json"
    if not app_json.is_file():
        return None
    try:
        data = json.loads(app_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    setting = data.get("attachmentFolderPath")
    if not isinstance(setting, str) or not setting.strip():
        return None
    return setting.strip()


def resolve_attachment_directory(
    vault_path: str,
    note_directory: str,
    attachment_folder_setting: str | None,
) -> str | None:
    """Map Obsidian's attachmentFolderPath setting to an absolute directory."""
    if not attachment_folder_setting:
        return None

    setting = attachment_folder_setting.strip().replace("/", os.sep)
    if setting in (".", f".{os.sep}"):
        return os.path.normpath(note_directory)
    if setting.startswith(f".{os.sep}"):
        return os.path.normpath(os.path.join(note_directory, setting[2:]))
    return os.path.normpath(os.path.join(vault_path, setting))

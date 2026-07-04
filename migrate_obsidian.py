#!/usr/bin/env python3
"""Migrate root-level markdown notes from one folder into a single Google Doc."""

from obsidian_to_gdrive.auth import get_google_services, validate_configuration
from obsidian_to_gdrive.migrator import run_migration


def main() -> None:
    # --- Configuration ---
    note_folder_path = r"C:\path\to\note\folder"
    google_doc_path = "Folder/Document Name"

    credentials_path = r"C:\path\to\credentials\client_secret.json"
    token_folder = r"C:\path\to\token"
    token_path = f"{token_folder}/token.json"

    validate_configuration(note_folder_path, credentials_path, token_folder)

    print("Initializing Google API Services...")
    docs_service, drive_service = get_google_services(credentials_path, token_path)
    run_migration(docs_service, drive_service, note_folder_path, google_doc_path)


if __name__ == "__main__":
    main()

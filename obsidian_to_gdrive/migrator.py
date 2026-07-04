import os
import re

from .constants import MIGRATION_ENGINE_VERSION
from .docs_builder import (
    _find_tab,
    build_markdown_requests_from_blocks,
    build_table_cell_insert_requests,
    build_table_cell_style_requests,
    find_table_at_or_after_index,
    get_index_after_table,
    make_insert_text_request,
)
from .drive_client import (
    UploadedDriveImage,
    ensure_doc_images_folder,
    ensure_my_drive_folder_path,
    find_drive_doc,
    format_missing_image_text,
    materialize_data_uri_image,
    resolve_image_path,
    retry_pending_image_revocations,
    try_revoke_public_image_access,
    upload_image,
)
from .markdown_parser import BlockKind, parse_markdown_blocks, strip_frontmatter
from .throttle import throttle_and_execute
from .vault import (
    collect_root_markdown_files,
    find_obsidian_vault_root,
    load_obsidian_attachment_folder,
    make_tab_title,
    split_drive_parent_path,
)


def get_first_tab_id(docs_service, doc_id: str) -> str:
    def _get():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get)
    tabs = doc.get("tabs", [])
    if not tabs:
        raise RuntimeError(f"No tabs returned for document {doc_id}.")
    tab_id = tabs[0].get("tabProperties", {}).get("tabId")
    if not tab_id:
        raise RuntimeError(f"No tab id returned for document {doc_id}.")
    return tab_id


def get_existing_tab_titles(docs_service, doc_id: str) -> set[str]:
    def _get():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get)
    titles: set[str] = set()
    for tab in doc.get("tabs", []):
        title = tab.get("tabProperties", {}).get("title")
        if title:
            titles.add(title)
    return titles


def get_tab_insert_index(docs_service, doc_id: str, tab_id: str) -> int:
    def _get():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get)
    tab = _find_tab(doc, tab_id)
    content = tab.get("documentTab", {}).get("body", {}).get("content", [])
    if not content:
        return 1
    return content[-1].get("endIndex", 2) - 1


def execute_requests(
    docs_service,
    doc_id: str,
    requests: list[dict],
    context: str = "",
) -> None:
    if not requests:
        return
    try:
        throttle_and_execute(
            lambda: docs_service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": requests})
            .execute()
        )
    except Exception as exc:
        request_kinds = [next(iter(request)) for request in requests]
        label = f" ({context})" if context else ""
        raise RuntimeError(
            f"Docs batchUpdate failed{label}: {len(requests)} request(s) "
            f"[{', '.join(request_kinds[:8])}"
            f"{', ...' if len(request_kinds) > 8 else ''}]: {exc}"
        ) from exc


def insert_table_block(
    docs_service,
    doc_id: str,
    tab_id: str,
    table,
    insert_index: int,
) -> int:
    insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
    rows = len(table.table_rows)
    columns = max(len(row) for row in table.table_rows)

    execute_requests(
        docs_service,
        doc_id,
        [
            {
                "insertTable": {
                    "rows": rows,
                    "columns": columns,
                    "location": {"tabId": tab_id, "index": insert_index},
                }
            }
        ],
    )

    def _get_doc():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get_doc)
    table_element = find_table_at_or_after_index(doc, tab_id, insert_index)
    cell_insert_requests = build_table_cell_insert_requests(
        doc, tab_id, table, insert_index
    )

    if cell_insert_requests:
        execute_requests(docs_service, doc_id, cell_insert_requests)
        doc = throttle_and_execute(_get_doc)
        table_element = find_table_at_or_after_index(doc, tab_id, insert_index)
        if table_element:
            style_requests = build_table_cell_style_requests(table_element, table, tab_id)
            if style_requests:
                execute_requests(docs_service, doc_id, style_requests)

    doc = throttle_and_execute(_get_doc)
    table_element = find_table_at_or_after_index(doc, tab_id, insert_index)
    if table_element:
        return get_index_after_table(doc, tab_id, table_element)
    return get_tab_insert_index(docs_service, doc_id, tab_id)


def flush_text_batch(
    docs_service,
    doc_id: str,
    text_batch: list,
    tab_id: str,
    insert_index: int,
) -> int:
    text_requests, list_batches, _length = build_markdown_requests_from_blocks(
        text_batch, tab_id, insert_index
    )
    if text_requests:
        execute_requests(docs_service, doc_id, text_requests)
    for batch in list_batches:
        execute_requests(docs_service, doc_id, batch)
    return get_tab_insert_index(docs_service, doc_id, tab_id)


def append_markdown_content(
    docs_service,
    doc_id: str,
    markdown: str,
    tab_id: str,
    insert_index: int,
) -> int:
    blocks = parse_markdown_blocks(markdown)
    text_batch: list = []

    for block in blocks:
        if block.kind == BlockKind.TABLE:
            if text_batch:
                insert_index = flush_text_batch(
                    docs_service, doc_id, text_batch, tab_id, insert_index
                )
                text_batch.clear()

            insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
            insert_index = insert_table_block(docs_service, doc_id, tab_id, block, insert_index)
            continue

        text_batch.append(block)

    if text_batch:
        insert_index = flush_text_batch(
            docs_service, doc_id, text_batch, tab_id, insert_index
        )

    return insert_index


def process_content_and_images(
    docs_service,
    doc_id: str,
    content: str,
    note_directory: str,
    vault_root: str,
    tab_id: str,
    images_folder_id: str,
    drive_service,
    pending_image_revocations: list[UploadedDriveImage],
    attachment_folder_setting: str | None = None,
) -> None:
    insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)

    img_regex = re.compile(
        r"!\[\[(?P<wiki>[^\]|]+)(?:\|[^\]]*)?\]\]|!\[(?P<alt>[^\]]*)\]\((?P<md>[^)]+)\)"
    )

    last_index = 0
    for match in img_regex.finditer(content):
        if match.start() > last_index:
            insert_index = append_markdown_content(
                docs_service,
                doc_id,
                content[last_index : match.start()],
                tab_id,
                insert_index,
            )

        img_ref = match.group("wiki") if match.group("wiki") else match.group("md")
        alt_text = match.group("alt")
        if not alt_text or not alt_text.strip():
            alt_text = os.path.splitext(img_ref.split("|")[0])[0]

        if img_ref.lower().startswith(("http://", "https://")):
            insert_index = append_markdown_content(
                docs_service,
                doc_id,
                f"[{alt_text}]({img_ref.strip()})\n",
                tab_id,
                insert_index,
            )
            last_index = match.end()
            continue

        temp_image_path: str | None = None
        if img_ref.lower().startswith("data:"):
            print(f"  Decoding embedded image: {alt_text}")
            temp_image_path = materialize_data_uri_image(img_ref)
            if temp_image_path is None:
                print(f"  Warning: could not decode embedded image '{alt_text}'")

        absolute_img_path = temp_image_path or resolve_image_path(
            img_ref, note_directory, vault_root, attachment_folder_setting
        )
        uploaded = (
            upload_image(drive_service, absolute_img_path, images_folder_id)
            if absolute_img_path
            else None
        )

        if uploaded:
            try:
                execute_requests(
                    docs_service,
                    doc_id,
                    [
                        {
                            "insertInlineImage": {
                                "uri": f"https://drive.google.com/uc?export=view&id={uploaded.file_id}",
                                "location": {"tabId": tab_id, "index": insert_index},
                            }
                        },
                    ],
                    context=f"inline image '{alt_text}'",
                )
                insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
                execute_requests(
                    docs_service,
                    doc_id,
                    [make_insert_text_request("\n", tab_id, insert_index)],
                    context=f"newline after image '{alt_text}'",
                )
                insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
            finally:
                if not try_revoke_public_image_access(drive_service, uploaded):
                    pending_image_revocations.append(uploaded)
                if temp_image_path:
                    try:
                        os.unlink(temp_image_path)
                    except OSError:
                        pass
        else:
            if temp_image_path:
                try:
                    os.unlink(temp_image_path)
                except OSError:
                    pass
            insert_index = append_markdown_content(
                docs_service,
                doc_id,
                format_missing_image_text(alt_text, img_ref),
                tab_id,
                insert_index,
            )

        last_index = match.end()

    if last_index < len(content):
        append_markdown_content(
            docs_service,
            doc_id,
            content[last_index:],
            tab_id,
            insert_index,
        )


def execute_requests_with_reply(docs_service, doc_id: str, requests: list[dict]) -> dict:
    return throttle_and_execute(
        lambda: docs_service.documents()
        .batchUpdate(documentId=doc_id, body={"requests": requests})
        .execute()
    )


def ensure_google_doc(
    docs_service,
    drive_service,
    doc_title: str,
    parent_folder_id: str,
) -> tuple[str, bool]:
    """Return (document_id, created_new)."""
    existing_doc = find_drive_doc(drive_service, doc_title, parent_folder_id)
    if existing_doc:
        print(f"Using existing document: '{doc_title}' [{existing_doc.id}]")
        return existing_doc.id, False

    created = throttle_and_execute(
        lambda: docs_service.documents().create(body={"title": doc_title}).execute()
    )
    doc_id = created["documentId"]

    current = throttle_and_execute(
        lambda: drive_service.files()
        .get(fileId=doc_id, fields="parents")
        .execute()
    )
    parents = current.get("parents", [])
    throttle_and_execute(
        lambda: drive_service.files()
        .update(
            fileId=doc_id,
            addParents=parent_folder_id,
            removeParents=",".join(parents),
            fields="id",
        )
        .execute()
    )

    print(f"Created document: '{doc_title}' [ID: {doc_id}]")
    return doc_id, True


def add_tab_for_note(
    docs_service,
    doc_id: str,
    tab_title: str,
    use_first_tab: bool,
) -> str:
    if use_first_tab:
        tab_id = get_first_tab_id(docs_service, doc_id)
        execute_requests(
            docs_service,
            doc_id,
            [
                {
                    "updateDocumentTabProperties": {
                        "tabProperties": {"tabId": tab_id, "title": tab_title},
                        "fields": "title",
                    }
                }
            ],
        )
        return tab_id

    result = execute_requests_with_reply(
        docs_service,
        doc_id,
        [
            {
                "addDocumentTab": {
                    "tabProperties": {"title": tab_title},
                }
            }
        ],
    )
    tab_id = result["replies"][0]["addDocumentTab"]["tabProperties"]["tabId"]
    if not tab_id:
        raise RuntimeError(f"Failed to create tab for '{tab_title}'.")
    return tab_id


def run_migration(
    docs_service,
    drive_service,
    note_folder_path: str,
    google_doc_path: str,
) -> None:
    note_folder = os.path.normpath(note_folder_path)
    md_files = collect_root_markdown_files(note_folder)
    if not md_files:
        print("No markdown files found at the root of the note folder.")
        return

    parent_path, doc_title = split_drive_parent_path(google_doc_path)
    if not doc_title:
        raise ValueError("google_doc_path must include a document name.")

    parent_folder_id = ensure_my_drive_folder_path(drive_service, parent_path)
    if parent_path:
        print(f"Drive parent folder: '{parent_path}' [{parent_folder_id}]")
    else:
        print("Drive parent folder: My Drive root")

    vault_root = find_obsidian_vault_root(note_folder)
    attachment_folder_setting = load_obsidian_attachment_folder(vault_root)
    if attachment_folder_setting:
        print(f"Obsidian attachment folder: '{attachment_folder_setting}'")

    doc_id, is_new_doc = ensure_google_doc(
        docs_service, drive_service, doc_title, parent_folder_id
    )
    images_folder_id = ensure_doc_images_folder(drive_service, parent_folder_id, doc_title)

    existing_tab_titles = get_existing_tab_titles(docs_service, doc_id)
    use_first_tab = is_new_doc

    print(
        f"\nProcessing note folder: '{note_folder}' "
        f"({len(md_files)} markdown file(s) -> '{google_doc_path}')"
    )
    print(f"Migration engine version: {MIGRATION_ENGINE_VERSION}")
    print(f"Loaded from: {os.path.dirname(os.path.abspath(__file__))}")

    pending_image_revocations: list[UploadedDriveImage] = []
    migrated_tabs = 0
    skipped_tabs = 0
    failed_tabs = 0

    try:
        for file_path in md_files:
            tab_title = make_tab_title(file_path)
            if tab_title in existing_tab_titles:
                print(f"  -> Skipping (tab exists): {tab_title}")
                skipped_tabs += 1
                continue

            print(f"  -> Migrating: {tab_title}")

            try:
                with open(file_path, encoding="utf-8") as f:
                    raw_content = f.read()
                clean_content = strip_frontmatter(raw_content)
                note_directory = os.path.dirname(file_path) or note_folder

                tab_id = add_tab_for_note(
                    docs_service, doc_id, tab_title, use_first_tab=use_first_tab
                )
                use_first_tab = False

                process_content_and_images(
                    docs_service,
                    doc_id,
                    clean_content,
                    note_directory,
                    vault_root,
                    tab_id,
                    images_folder_id,
                    drive_service,
                    pending_image_revocations,
                    attachment_folder_setting,
                )
                existing_tab_titles.add(tab_title)
                migrated_tabs += 1
            except Exception as exc:
                failed_tabs += 1
                print(f"  ERROR migrating '{tab_title}': {exc}")

        print(
            f"\nMigration finished: {migrated_tabs} tab(s) added, "
            f"{skipped_tabs} skipped, {failed_tabs} failed."
        )
    finally:
        retry_pending_image_revocations(drive_service, pending_image_revocations)

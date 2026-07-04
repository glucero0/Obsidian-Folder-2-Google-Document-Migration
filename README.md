# Obsidian Notes to Google Doc Migration

Migrate root-level markdown notes from a single Obsidian folder into one Google Doc on Drive. Each `.md` file at the root of that folder becomes a tab in the document. Folder structure, headings, lists, tables, links, and embedded images are preserved where supported.

Created and maintained by **Cursor Composer 2.5 Standard**, with direction from **Gary Lucero**.

## What it does

- Reads every `.md` file at the **root** of a configured local folder (subfolders are not scanned).
- Creates or opens a single Google Doc at a configurable path in **My Drive**.
- Adds each markdown file as a **tab** inside that doc (tab title = filename without `.md`).
- Skips tabs that already exist in the document so you can re-run safely to pick up new notes.
- Uploads embedded images to a per-document `_images/<doc title>/` folder on Drive and inserts them into the doc.
- Strips YAML frontmatter from `.md` files.

## Requirements

- **Python 3.10+** (the code uses modern type syntax such as `str | None`)
- A **local folder** containing Obsidian markdown notes
- A **Google Cloud project** with the Google Docs API and Google Drive API enabled
- OAuth 2.0 **Desktop app** credentials downloaded as a JSON file
- All filenames must be 50 characters or less (limitation of Google Docs tabs)

## Google Cloud setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select an existing one).
3. Enable these APIs for the project:
   - [Google Docs API](https://console.cloud.google.com/apis/library/docs.googleapis.com)
   - [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)
4. Configure the **OAuth consent screen** (External or Internal, depending on your Google account type).
5. Create **OAuth 2.0 Client ID** credentials:
   - Application type: **Desktop app**
   - Download the JSON file and store it somewhere safe on your machine.
6. The first time you run the script, a browser window opens for Google sign-in. After authorization, a `token.json` file is saved locally for future runs.

The script requests these OAuth scopes:

- `https://www.googleapis.com/auth/documents`
- `https://www.googleapis.com/auth/drive`

## Installation

Clone or download this repository, then install dependencies:

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

All user-facing settings are in `migrate_obsidian.py`. Open that file and edit the values in the **Configuration** section:

| Setting | Description |
| --- | --- |
| `note_folder_path` | Absolute path to the folder whose root-level `.md` files should be migrated |
| `credentials_path` | Absolute path to your Google OAuth client JSON file |
| `token_folder` | Directory where `token.json` will be stored after first login |
| `token_path` | Full path to the saved OAuth token (usually `{token_folder}/token.json`) |
| `google_doc_path` | Path and filename of the target Google Doc in My Drive (folders are created as needed) |

Example:

```python
note_folder_path = r"C:\Users\you\ObsidianVaults\Personal\Projects\Website"
credentials_path = r"C:\Obsidian Migration\client_secret.json"
token_folder = r"C:\Obsidian Migration\TokenStore"
token_path = f"{token_folder}/token.json"
google_doc_path = "Notebooks/Website"
```

### Drive output layout

- The document is always created under **My Drive**.
- `google_doc_path` uses `/`-style segments. The last segment is the document title; earlier segments are folders created as needed.
- Images: `<parent folder>/_images/<doc title>/`

Example local folder:

```text
C:\Users\you\ObsidianVaults\Personal\Projects\Website\
  index.md
  todo.md
  assets\          ← not scanned (only root-level .md files)
```

With `google_doc_path = "Notebooks/Website"`, Drive result:

```text
My Drive/
  Notebooks/
    Website          ← Google Doc titled "Website"
      ├─ tab: index
      └─ tab: todo
    _images/
      Website/       ← uploaded images for that doc
```

## Running the migration

From the repository root with your virtual environment activated:

```bash
python migrate_obsidian.py
```

On first run:

1. The script validates that the note folder and credentials file exist.
2. A browser opens for Google OAuth (unless a valid `token.json` already exists).
3. Migration progress is printed to the console.

The script is rate-limited to stay under Google API quotas (roughly one API call every ~1.1 seconds). Large notes can take a while.

## Markdown support

Supported elements include:

- Headings (`#` through `######`)
- Bold, italic, bold+italic, strikethrough, inline code
- Bullet and numbered lists (with nesting)
- Block quotes and fenced code blocks
- Markdown tables
- Standard links `[text](url)` and Obsidian wiki links `[[Note]]` / `[[Note|alias]]`
- Images: `![alt](path)`, including paths resolved via Obsidian's `attachmentFolderPath` setting in `.obsidian/app.json` (when a vault root is found above the note folder)
- PNG, JPG/JPEG, and GIF images (including some `data:` URI embedded images)

YAML frontmatter at the top of a note is stripped before conversion.

## Re-running safely

If you run the script again:

- The existing Google Doc is reused.
- Tabs whose titles already match a root-level `.md` filename are **skipped**.
- New `.md` files get new tabs appended to the document.

To re-migrate a single note, delete its tab in Google Docs first, then run the script again.

## Security notes

- **Do not commit** OAuth credentials or tokens. This repository's `.gitignore` excludes common secret filenames (`credentials.json`, `client_secret*.json`, `token.json`, `TokenStore/`, etc.).
- Keep your OAuth client JSON and `token.json` on your local machine only.
- The script temporarily grants public read access to uploaded images so Google Docs can embed them, then revokes that access after insertion.

## Troubleshooting

| Problem | Things to check |
| --- | --- |
| `Note folder path not found` | `note_folder_path` in `migrate_obsidian.py` points to an existing folder |
| `Credentials file not found` | `credentials_path` points to the downloaded OAuth JSON |
| Browser does not open / auth fails | APIs enabled, consent screen configured, Desktop client type used |
| Images missing | Image path relative to note or Obsidian attachment folder; supported format (PNG/JPG/GIF) |
| Slow migration | Expected — API throttling is intentional for quota safety |

## Project layout

```text
migrate_obsidian.py          Entry point and configuration
obsidian_to_gdrive/
  auth.py                    Google OAuth and service setup
  migrator.py                Migration orchestration
  vault.py                   Note folder scanning and path logic
  drive_client.py            Drive folder/file operations
  docs_builder.py            Google Docs API request building
  markdown_parser.py         Markdown parsing
  constants.py                 Internal defaults (scopes, rate limits, etc.)
requirements.txt             Python dependencies
```

## License

MIT License

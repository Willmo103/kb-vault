import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import sqlite_utils
from kb_core.utils import (
    build_tree_string,
    get_uuid,
    hash_content,
    read_file_text,
    should_ignore_path,
)

logger = logging.getLogger(__name__)


def find_obsidian_vaults(root: Path) -> Iterable[Path]:
    """Yield parent directories of any .obsidian folder found under root recursively."""
    try:
        for entry in os.scandir(root):
            p = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False):
                continue
            if should_ignore_path(p.resolve()):
                continue
            if (p / ".obsidian").is_dir():
                yield p
                continue  # skip scanning inside a vault
            yield from find_obsidian_vaults(p)
    except (PermissionError, FileNotFoundError) as exc:
        logger.warning("Error accessing path %s: %s", root, exc)


def record_needs_update(file_path: str, vault_path: str, db: sqlite_utils.Database) -> bool:
    """Check if the record for file_path needs to be updated based on modification time."""
    sql = "SELECT modified FROM vault_files WHERE file_path = ? AND vault_path = ? LIMIT 1"
    result = db.execute_returning_dicts(sql, [file_path, vault_path])
    if not result:
        return True
    try:
        existing_modified = datetime.fromtimestamp(Path(file_path).stat().st_mtime)
        db_modified = datetime.fromisoformat(result[0]["modified"])
        return existing_modified > db_modified
    except OSError:
        return False


def discover_files(vault_path: Path, db: sqlite_utils.Database) -> list[dict]:
    """Return a list of dicts containing file metadata for .md files in vault_path."""
    records = []
    vault_path_str = vault_path.as_posix()
    for root, dirs, files in os.walk(vault_path):
        # Allow .obsidian to be traversed or skipped as metadata, but skip other system dirs
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"}]

        for name in files:
            fp = Path(root) / name
            if fp.suffix.lower() != ".md":
                continue
            if should_ignore_path(fp.resolve()):
                continue

            file_path_str = fp.as_posix()
            try:
                st = fp.stat()
            except OSError as exc:
                logger.warning("stat failed for %s: %s", fp, exc)
                continue

            # Check if record needs update
            if not record_needs_update(file_path_str, vault_path_str, db):
                continue

            records.append(
                {
                    "uuid": get_uuid(),
                    "vault_path": vault_path_str,
                    "file_path": file_path_str,
                    "file_name": name,
                    "extension": fp.suffix.lower(),
                    "relative_path": fp.relative_to(vault_path).as_posix(),
                    "size": st.st_size,
                    "created": datetime.fromtimestamp(st.st_birthtime if hasattr(st, "st_birthtime") else st.st_ctime).isoformat(),
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "mode": oct(st.st_mode),
                    "last_scanned": datetime.now().isoformat(),
                }
            )
    return records


def scan_and_index_vaults(scan_paths: list[str], db: sqlite_utils.Database, dry_run: bool = False) -> int:
    """Find Obsidian vaults, record their metadata, scan files, and import contents."""
    start_time = time.time()
    logger.info("Starting scan_and_index_vaults, dry_run=%s", dry_run)

    # 1. Discover vault paths
    discovered_vaults: list[Path] = []
    for path_str in scan_paths:
        p = Path(path_str)
        if not p.is_dir():
            logger.warning("Scan path is not a directory: %s", path_str)
            continue
        discovered_vaults.extend(find_obsidian_vaults(p))

    logger.info("Found %d vault paths on disk", len(discovered_vaults))
    if dry_run:
        print(f"Dry-run: Discovered {len(discovered_vaults)} Obsidian vaults.")
        return len(discovered_vaults)

    # 2. Store vault paths metadata
    for vault in discovered_vaults:
        vault_path_str = vault.as_posix()
        try:
            st = vault.stat()
        except OSError:
            continue

        existing = db.execute_returning_dicts("SELECT * FROM vault_paths WHERE path = ?", [vault_path_str])
        modified_time = datetime.fromtimestamp(st.st_mtime).isoformat()
        if existing:
            db["vault_paths"].update(
                vault_path_str,
                {
                    "last_scanned": datetime.now().isoformat(),
                    "modified": modified_time,
                }
            )
        else:
            db["vault_paths"].insert(
                {
                    "uuid": get_uuid(),
                    "name": vault.name,
                    "path": vault_path_str,
                    "created": datetime.fromtimestamp(st.st_birthtime if hasattr(st, "st_birthtime") else st.st_ctime).isoformat(),
                    "modified": modified_time,
                    "tree": "",
                    "last_scanned": datetime.now().isoformat(),
                },
                pk="path",
                replace=True,
            )

    # 3. Scan and store file metadata, building trees
    all_file_records = []
    for vault in discovered_vaults:
        vault_path_str = vault.as_posix()
        files = discover_files(vault, db)

        # Generate and save ASCII tree string
        existing_files = db.execute_returning_dicts("SELECT relative_path FROM vault_files WHERE vault_path = ?", [vault_path_str])
        all_rel_paths = list(set([f["relative_path"] for f in existing_files] + [f["relative_path"] for f in files]))
        tree = build_tree_string(vault_path_str, all_rel_paths)
        db["vault_paths"].update(vault_path_str, {"tree": tree})

        all_file_records.extend(files)
        logger.info("Scanned vault %s, found %d modified/new files", vault_path_str, len(files))

    if all_file_records:
        db["vault_files"].insert_all(all_file_records, replace=True)
        logger.info("Stored %d file metadata records", len(all_file_records))
        print(f"Stored {len(all_file_records)} file metadata records.")

    # 4. Import file contents for files with missing or updated contents
    # Query files in vault_files that do not have matching contents in vault_file_contents
    query = """
        SELECT vf.vault_path, vf.file_path
        FROM vault_files vf
        LEFT JOIN vault_file_contents fc ON fc.file_path = vf.file_path AND fc.vault_path = vf.vault_path
        WHERE fc.content_hash IS NULL;
    """
    missing_contents = db.execute_returning_dicts(query)
    
    # Combine missing files and files that were modified/added in this scan run
    to_import_set = {(row["vault_path"], row["file_path"]) for row in missing_contents}
    for vf in all_file_records:
        to_import_set.add((vf["vault_path"], vf["file_path"]))

    logger.info("Found %d files needing content import/update", len(to_import_set))

    content_inserts = 0
    for vault_path_str, file_path_str in to_import_set:
        fp = Path(file_path_str)
        if not fp.is_file():
            continue

        content = read_file_text(fp)
        current_hash = hash_content(content)

        # Check update necessity
        existing = db.execute_returning_dicts(
            "SELECT uuid, content_hash FROM vault_file_contents WHERE file_path = ? AND vault_path = ? LIMIT 1",
            [file_path_str, vault_path_str]
        )
        if existing:
            if existing[0]["content_hash"] != current_hash:
                db["vault_file_contents"].update(
                    [vault_path_str, file_path_str],
                    {
                        "content": content,
                        "content_hash": current_hash,
                    }
                )
                content_inserts += 1
        else:
            db["vault_file_contents"].insert(
                {
                    "uuid": get_uuid(),
                    "vault_path": vault_path_str,
                    "file_path": file_path_str,
                    "content": content,
                    "content_hash": current_hash,
                    "version": 1,
                },
                replace=True,
            )
            content_inserts += 1

    logger.info("Successfully imported %d file contents in %.2f seconds", content_inserts, time.time() - start_time)
    print(f"Imported/updated {content_inserts} file contents.")
    return len(discovered_vaults)

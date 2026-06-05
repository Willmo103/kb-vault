import json
from pathlib import Path
import pytest
import sqlite_utils

import kb_vault.process
from kb_vault.config import Config
from kb_vault.database import init_vault_db
from kb_vault.process import find_obsidian_vaults, scan_and_index_vaults


@pytest.fixture(autouse=True)
def mock_should_ignore_path(monkeypatch):
    """Monkeypatch should_ignore_path to prevent skipping system temp folders during tests."""
    from kb_core.utils import should_ignore_path as original_should_ignore
    import tempfile

    temp_dir = Path(tempfile.gettempdir()).resolve()

    def fake_should_ignore(path):
        resolved_path = Path(path).resolve()
        if temp_dir in resolved_path.parents or temp_dir == resolved_path:
            # For testing inside system temp directories, only check standard git/build dirs
            name = resolved_path.name
            if name in (".git", "node_modules", "__pycache__", ".venv", ".obsidian"):
                return True
            if resolved_path.suffix.lower() in (".pyc", ".pyo"):
                return True
            return False
        return original_should_ignore(path)

    monkeypatch.setattr(kb_vault.process, "should_ignore_path", fake_should_ignore)


@pytest.fixture
def temp_kb_dir(tmp_path):
    """Fixture to setup a temporary ~/.kb environment."""
    root = tmp_path / ".kb"
    configs = root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    return root


def test_config_load_and_save(temp_kb_dir):
    cfg = Config()
    cfg.root = temp_kb_dir
    cfg.configs_dir = temp_kb_dir / "configs"
    cfg.fallback_config_path = temp_kb_dir / "nonexistent_scan_config.json"

    # Test default
    vault_cfg = cfg.load_vault_config()
    assert "scan_paths" in vault_cfg
    assert vault_cfg["scan_paths"] == []

    # Test save and load
    vault_cfg["scan_paths"] = ["/some/vault/path"]
    cfg.save_vault_config(vault_cfg)

    loaded = cfg.load_vault_config()
    assert loaded["scan_paths"] == ["/some/vault/path"]


def test_database_initialization(temp_kb_dir):
    db_path = temp_kb_dir / "kb.db"
    db = sqlite_utils.Database(str(db_path))
    init_vault_db(db)

    # Verify tables
    assert "vault_paths" in db.table_names()
    assert "vault_files" in db.table_names()
    assert "vault_file_contents" in db.table_names()
    assert "content_versions" in db.table_names()

    # Verify views
    assert "vaults" in db.view_names()
    assert "vault_master" in db.view_names()


def test_find_obsidian_vaults(tmp_path):
    # Setup folders
    vault1 = tmp_path / "vault1"
    vault1.mkdir()
    (vault1 / ".obsidian").mkdir()

    vault2 = tmp_path / "subdir" / "vault2"
    vault2.mkdir(parents=True)
    (vault2 / ".obsidian").mkdir()

    non_vault = tmp_path / "non_vault"
    non_vault.mkdir()

    found = list(find_obsidian_vaults(tmp_path))
    found_paths = [p.as_posix() for p in found]

    assert vault1.as_posix() in found_paths
    assert vault2.as_posix() in found_paths
    assert non_vault.as_posix() not in found_paths


def test_scan_and_index_vaults(temp_kb_dir, tmp_path):
    # Create test vault
    vault = tmp_path / "personal_notes"
    vault.mkdir()
    (vault / ".obsidian").mkdir()

    # Create test notes
    note_file = vault / "daily_log.md"
    note_file.write_text("# Daily Log\nSome text.", encoding="utf-8")

    ignored_file = vault / "image.png"
    ignored_file.write_text("binary-data", encoding="utf-8")

    db_path = temp_kb_dir / "kb.db"
    db = sqlite_utils.Database(str(db_path))
    init_vault_db(db)

    # Perform scan
    scan_and_index_vaults([tmp_path.as_posix()], db)

    # Verify db records
    vaults = db.execute_returning_dicts("SELECT * FROM vault_paths")
    assert len(vaults) == 1
    assert vaults[0]["name"] == "personal_notes"
    assert vaults[0]["path"] == vault.as_posix()

    files = db.execute_returning_dicts("SELECT * FROM vault_files")
    assert len(files) == 1
    assert files[0]["file_name"] == "daily_log.md"

    contents = db.execute_returning_dicts("SELECT * FROM vault_file_contents")
    assert len(contents) == 1
    assert contents[0]["content"] == "# Daily Log\nSome text."
    assert contents[0]["version"] == 1

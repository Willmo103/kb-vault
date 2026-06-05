import logging
import sqlite_utils

logger = logging.getLogger(__name__)


def init_vault_db(db: sqlite_utils.Database):
    """Initialize all vault-related tables, indices, triggers, and views in the SQLite database."""

    # 1. vault_paths table
    if "vault_paths" not in db.table_names():
        db["vault_paths"].create(
            {
                "uuid": str,
                "name": str,
                "path": str,
                "created": str,
                "modified": str,
                "tree": str,
                "last_scanned": str,
            },
            pk="path",
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_paths_path ON vault_paths(path);"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_paths_uuid ON vault_paths(uuid);"
        )

    # 2. vault_files table
    if "vault_files" not in db.table_names():
        db.create_table(
            "vault_files",
            {
                "uuid": str,
                "vault_path": str,
                "file_path": str,
                "file_name": str,
                "extension": str,
                "relative_path": str,
                "size": int,
                "created": str,
                "modified": str,
                "mode": str,
                "last_scanned": str,
            },
            pk=("vault_path", "file_path"),
            foreign_keys=[("vault_path", "vault_paths", "path")],
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_file_meta_path ON vault_files(file_path);"
        )

    # 3. content_versions table (common or standalone)
    if "content_versions" not in db.table_names():
        db.create_table(
            "content_versions",
            {
                "source": str,
                "file_path": str,
                "content": str,
                "content_hash": str,
                "version": int,
            },
            pk=("source", "file_path", "version"),
        )

    # 4. vault_file_contents table (with FTS enabled)
    if "vault_file_contents" not in db.table_names():
        db.create_table(
            "vault_file_contents",
            {
                "uuid": str,
                "vault_path": str,
                "file_path": str,
                "content": str,
                "content_hash": str,
                "version": int,
            },
            pk=("vault_path", "file_path"),
            foreign_keys=[("vault_path", "vault_paths", "path")],
        )
        db["vault_file_contents"].enable_fts(["content"])
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_file_content_hash ON vault_file_contents(content_hash);"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_file_content_path ON vault_file_contents(file_path);"
        )

        # Triggers
        sql_trigger_update = """
        CREATE TRIGGER IF NOT EXISTS copy_vault_file_to_versions_table_on_hash_change
        AFTER UPDATE OF content_hash ON vault_file_contents
        WHEN NEW.content_hash != OLD.content_hash
        BEGIN
            INSERT INTO content_versions (source, file_path, content, content_hash, version)
            VALUES (OLD.vault_path, OLD.file_path, OLD.content, OLD.content_hash, COALESCE((SELECT MAX(version) FROM content_versions WHERE source = OLD.vault_path AND file_path = OLD.file_path), 0) + 1);
            UPDATE vault_file_contents SET version = COALESCE((SELECT MAX(version) FROM content_versions WHERE source = NEW.vault_path AND file_path = NEW.file_path), 0) + 1 WHERE vault_path = NEW.vault_path AND file_path = NEW.file_path;
        END;
        """
        db.execute(sql_trigger_update)

        sql_trigger_delete = """
        CREATE TRIGGER IF NOT EXISTS delete_vault_file_versions_on_file_delete
        AFTER DELETE ON vault_file_contents
        BEGIN
            DELETE FROM content_versions WHERE source = OLD.vault_path AND file_path = OLD.file_path;
        END;
        """
        db.execute(sql_trigger_delete)

    # 5. vault_file_descriptions / vault_descriptions / vault_tags / vault_file_tags tables
    if "vault_file_descriptions" not in db.table_names():
        db["vault_file_descriptions"].create(
            {
                "content_hash": str,
                "description": str,
                "vault_path": str,
                "file_path": str,
            },
            pk="content_hash",
        )
    if "vault_descriptions" not in db.table_names():
        db["vault_descriptions"].create(
            {
                "vault_uuid": str,
                "vault_path": str,
                "description": str,
            },
            pk="vault_uuid",
        )
    if "vault_tags" not in db.table_names():
        db["vault_tags"].create({"tag": str}, pk="tag")
    if "vault_file_tags" not in db.table_names():
        db["vault_file_tags"].create({"vault_path": str, "file_path": str, "tag": str})

    # 6. vault_file_neighbors table
    if "vault_file_neighbors" not in db.table_names():
        db["vault_file_neighbors"].create(
            {
                "source_content_hash": str,
                "source_vault_path": str,
                "source_file_path": str,
                "target_content_hash": str,
                "target_vault_path": str,
                "target_file_path": str,
                "similarity_score": float,
                "rank": int,
            },
            pk=("source_content_hash", "target_content_hash"),
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_neighbor_source ON vault_file_neighbors(source_content_hash);"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_neighbor_target ON vault_file_neighbors(target_content_hash);"
        )

    # 7. embeddings tables
    if "vault_file_description_embeddings" not in db.table_names():
        db["vault_file_description_embeddings"].create(
            {"vault_path": str, "file_path": str, "embedding": str},
            pk=("vault_path", "file_path"),
        )
    if "vault_file_embeddings" not in db.table_names():
        db["vault_file_embeddings"].create(
            {
                "content_hash": str,
                "vault_path": str,
                "file_path": str,
                "embedding": str,
            },
            pk="content_hash",
        )

    # 8. cleaned_markdown table
    if "cleaned_markdown" not in db.table_names():
        db["cleaned_markdown"].create(
            {
                "content_hash": str,
                "vault_path": str,
                "file_path": str,
                "cleaned_content": str,
            },
            pk="content_hash",
        )

    # 9. views
    if "empty_vault_files" not in db.view_names():
        db.create_view(
            "empty_vault_files",
            "SELECT vault_path, file_path FROM vault_files WHERE size = 0",
        )
    if "large_vault_files" not in db.view_names():
        db.create_view(
            "large_vault_files",
            "SELECT vault_path, file_path FROM vault_files WHERE size > 102400 ORDER BY size DESC",
        )
    if "vaults" not in db.view_names():
        db.create_view(
            "vaults",
            """
            SELECT
                vp.uuid,
                vp.name,
                vp.path,
                vp.tree,
                vp.created,
                COUNT(vf.file_path) AS files
            FROM vault_paths vp
            LEFT JOIN vault_files vf ON vp.path = vf.vault_path
            GROUP BY vp.uuid, vp.name, vp.path
            """,
        )
    if "vault_master" not in db.view_names():
        db.create_view(
            "vault_master",
            """
            SELECT
                vp.name as vault_name,
                vp.uuid as vp_uuid,
                vc.uuid as vfc_uuid,
                vf.uuid as vf_uuid,
                vf.relative_path,
                vf.created,
                vf.modified,
                vf.file_name,
                vf.extension,
                vf.size,
                vc.content_hash,
                vc.content,
                vc.version,
                fd.description
            FROM vault_files vf
            JOIN vault_paths vp ON vp.path = vf.vault_path
            LEFT JOIN vault_file_contents vc ON vf.vault_path = vc.vault_path and vf.file_path = vc.file_path
            LEFT JOIN vault_file_descriptions fd ON vc.content_hash = fd.content_hash
            WHERE vc.content IS NOT NULL
            """,
        )
    if "valid_vault_files" not in db.view_names():
        db.create_view(
            "valid_vault_files",
            """
            SELECT file_path, vault_path FROM vault_files
            WHERE size > 0 AND size <= 102400
            """,
        )

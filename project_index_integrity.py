from __future__ import annotations

import hashlib
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from db import DB_PATH


AUDIT_VERSION = "1.4-a0"
ISOLATED_COPY_SCOPE = "isolated_test_or_copy"
ROOT = Path(__file__).resolve().parent


class ProjectIndexAuditError(RuntimeError):
    pass


class ProjectIndexCleanupBlocked(PermissionError):
    pass


@dataclass(frozen=True)
class ProjectIndexAuditReport:
    schema_version: int
    audit_version: str
    database_label: str
    operation: str
    status: str
    is_clean: bool
    chunk_count: int
    fts_row_count: int
    matched_chunk_count: int
    issue_counts: dict[str, int]
    orphan_fts_rows: list[dict]
    missing_chunk_ids: list[int]
    duplicate_chunks: list[dict]
    malformed_fts_rows: list[dict]
    dangling_fts_rows: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProjectIndexCleanupReport:
    schema_version: int
    cleanup_version: str
    database_label: str
    operation: str
    status: str
    removed_fts_rowids: list[int]
    before: dict
    after: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical_positive_int(value: object) -> int | None:
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        return None
    return int(text)


def _require_index_tables(connection: sqlite3.Connection) -> None:
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    required = {"project_files", "project_file_chunks", "project_file_chunks_fts"}
    missing = sorted(required - names)
    if missing:
        raise ProjectIndexAuditError("项目索引表缺失：" + "、".join(missing))


def audit_project_index_connection(
    connection: sqlite3.Connection, *, database_label: str = "connection"
) -> ProjectIndexAuditReport:
    """Audit project FTS mappings using SELECT statements only."""
    _require_index_tables(connection)
    chunks = {
        int(row[0]): {"project_id": int(row[1]), "file_id": int(row[2])}
        for row in connection.execute(
            "SELECT id,project_id,file_id FROM project_file_chunks ORDER BY id"
        )
    }
    files = {
        int(row[0]): int(row[1])
        for row in connection.execute("SELECT id,project_id FROM project_files ORDER BY id")
    }
    fts_rows = list(
        connection.execute(
            "SELECT rowid,chunk_id,project_id,file_id "
            "FROM project_file_chunks_fts ORDER BY rowid"
        )
    )

    orphan_rows: list[dict] = []
    malformed_rows: list[dict] = []
    dangling_rows: list[dict] = []
    valid_by_chunk: dict[int, list[int]] = {}

    for row in fts_rows:
        rowid = int(row[0])
        raw_chunk_id, raw_project_id, raw_file_id = row[1], row[2], row[3]
        chunk_id = _canonical_positive_int(raw_chunk_id)
        project_id = _canonical_positive_int(raw_project_id)
        file_id = _canonical_positive_int(raw_file_id)
        if None in (chunk_id, project_id, file_id):
            malformed_rows.append(
                {
                    "fts_rowid": rowid,
                    "chunk_id": str(raw_chunk_id if raw_chunk_id is not None else ""),
                    "project_id": str(raw_project_id if raw_project_id is not None else ""),
                    "file_id": str(raw_file_id if raw_file_id is not None else ""),
                }
            )
            continue
        if chunk_id not in chunks:
            orphan_rows.append(
                {
                    "fts_rowid": rowid,
                    "chunk_id": chunk_id,
                    "project_id": project_id,
                    "file_id": file_id,
                }
            )
            continue

        actual = chunks[chunk_id]
        file_project_id = files.get(actual["file_id"])
        reasons = []
        if project_id != actual["project_id"] or file_id != actual["file_id"]:
            reasons.append("fts_chunk_metadata_mismatch")
        if file_project_id is None:
            reasons.append("project_file_missing")
        elif file_project_id != actual["project_id"]:
            reasons.append("chunk_file_project_mismatch")
        if reasons:
            dangling_rows.append(
                {
                    "fts_rowid": rowid,
                    "chunk_id": chunk_id,
                    "fts_project_id": project_id,
                    "fts_file_id": file_id,
                    "actual_project_id": actual["project_id"],
                    "actual_file_id": actual["file_id"],
                    "reasons": reasons,
                }
            )
            continue
        valid_by_chunk.setdefault(chunk_id, []).append(rowid)

    missing_chunk_ids = sorted(set(chunks) - set(valid_by_chunk))
    duplicate_chunks = [
        {"chunk_id": chunk_id, "fts_rowids": sorted(rowids)}
        for chunk_id, rowids in sorted(valid_by_chunk.items())
        if len(rowids) > 1
    ]
    issue_counts = {
        "orphan_fts_rows": len(orphan_rows),
        "missing_fts_rows": len(missing_chunk_ids),
        "duplicate_chunks": len(duplicate_chunks),
        "malformed_fts_rows": len(malformed_rows),
        "dangling_fts_rows": len(dangling_rows),
    }
    clean = not any(issue_counts.values())
    return ProjectIndexAuditReport(
        schema_version=1,
        audit_version=AUDIT_VERSION,
        database_label=database_label,
        operation="audit",
        status="clean" if clean else "issues_found",
        is_clean=clean,
        chunk_count=len(chunks),
        fts_row_count=len(fts_rows),
        matched_chunk_count=len(valid_by_chunk),
        issue_counts=issue_counts,
        orphan_fts_rows=orphan_rows,
        missing_chunk_ids=missing_chunk_ids,
        duplicate_chunks=duplicate_chunks,
        malformed_fts_rows=malformed_rows,
        dangling_fts_rows=dangling_rows,
    )


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    path = database_path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def audit_project_index(database_path: str | Path | None = None) -> ProjectIndexAuditReport:
    """Audit an index without creating or modifying the database.

    Omitting ``database_path`` targets the configured user database in read-only
    mode.  This is intentionally the only default production operation.
    """
    path = Path(database_path) if database_path is not None else Path(DB_PATH)
    connection = _readonly_connection(path)
    try:
        return audit_project_index_connection(connection, database_label=path.name)
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_isolated_copy(path: Path) -> bool:
    roots = [Path(tempfile.gettempdir()).resolve(), (ROOT / ".test-tmp").resolve()]
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def cleanup_orphan_fts_rows(
    database_path: str | Path,
    *,
    scope: str = "production",
) -> ProjectIndexCleanupReport:
    """Delete only orphan FTS rows from an isolated test/copy database.

    Production cleanup is deliberately not exposed as a default.  Callers must
    provide the isolated scope explicitly, and the resolved database must live
    under a temporary test/copy root and differ from the configured user DB.
    """
    path = Path(database_path).expanduser().resolve(strict=True)
    configured_db = Path(DB_PATH).expanduser().resolve()
    if scope != ISOLATED_COPY_SCOPE or path == configured_db or not _is_isolated_copy(path):
        raise ProjectIndexCleanupBlocked(
            "生产数据库仅允许 audit；定点 cleanup 仅限显式隔离的测试/复制数据库。"
        )

    before_hash = _sha256(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        before = audit_project_index_connection(connection, database_label=path.name)
        rowids = sorted(row["fts_rowid"] for row in before.orphan_fts_rows)
        if rowids:
            marks = ",".join("?" for _ in rowids)
            connection.execute(
                f"DELETE FROM project_file_chunks_fts WHERE rowid IN ({marks})", rowids
            )
        after = audit_project_index_connection(connection, database_label=path.name)
        remaining = set(row["fts_rowid"] for row in after.orphan_fts_rows) & set(rowids)
        if remaining:
            raise ProjectIndexAuditError(
                "定点 cleanup 校验失败，仍存在 orphan FTS row："
                + ",".join(str(value) for value in sorted(remaining))
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    # A changed hash proves the isolated copy, rather than the production DB,
    # was the mutation target.  No-op/idempotent cleanup is allowed.
    after_hash = _sha256(path)
    status = "clean" if after.is_clean else "remaining_issues"
    return ProjectIndexCleanupReport(
        schema_version=1,
        cleanup_version=AUDIT_VERSION,
        database_label=path.name,
        operation="targeted_orphan_cleanup",
        status=status,
        removed_fts_rowids=rowids,
        before={**before.to_dict(), "database_sha256": before_hash},
        after={**after.to_dict(), "database_sha256": after_hash},
    )
